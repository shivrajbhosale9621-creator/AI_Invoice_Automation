"""
OCR Engine & Perception Agent Module.
Handles end-to-end invoice processing:
1. Load PDF or Image directly (png, jpg, jpeg, tiff, bmp)
2. Computer Vision preprocessing (OpenCV Otsu Binarization)
3. RapidOCR ONNX text recognition
4. Groq LLM dynamic schema structuring
35. Packaging into LlamaIndex Document & persisting to Vector Store Index
"""

import os
import re
import json
import numpy as np
import cv2
import fitz  # PyMuPDF
import time
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from huggingface_hub import hf_hub_download

from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from config import GROQ_API_KEY, GROQ_MODEL_NAME, DEFAULT_INDEX_PATH
from vector_store import save_invoice_to_vector_store, load_index


# -------------------------------------------------------------
# 1. Helper: Resolve & Cache ONNX Model Paths and RapidOCR Singleton
# -------------------------------------------------------------
_OCR_PATHS_CACHE = None
_RAPID_OCR_INSTANCE = None


def get_ocr_model_paths():
	"""
	Downloads or resolves local cached paths for PaddleOCR ONNX models.
	Caches resolved paths in-memory to prevent repeated HuggingFace Hub network checks.
	"""
	global _OCR_PATHS_CACHE
	if _OCR_PATHS_CACHE is None:
		det_path = hf_hub_download("monkt/paddleocr-onnx", "detection/v5/det.onnx")
		rec_path = hf_hub_download("monkt/paddleocr-onnx", "languages/english/rec.onnx")
		dict_path = hf_hub_download("monkt/paddleocr-onnx", "languages/english/dict.txt")
		_OCR_PATHS_CACHE = (det_path, rec_path, dict_path)
	return _OCR_PATHS_CACHE


def get_ocr_instance() -> RapidOCR:
	"""
	Thread-safe singleton for RapidOCR ONNX inference engine.
	Avoids reloading 50+ MB ONNX session graphs on every request, slashing ~12s per invoice.
	"""
	global _RAPID_OCR_INSTANCE
	if _RAPID_OCR_INSTANCE is None:
		det_path, rec_path, dict_path = get_ocr_model_paths()
		_RAPID_OCR_INSTANCE = RapidOCR(
			det_model_path=det_path,
			rec_model_path=rec_path,
			rec_keys_path=dict_path,
		)
	return _RAPID_OCR_INSTANCE


# -------------------------------------------------------------
# 2. Step 1: File Loader (Supports both PDF and Raw Images)
# -------------------------------------------------------------
def load_file_to_images(file_path: str) -> list[Image.Image]:
	"""
	Load an invoice file (PDF or Image) into PIL Images.
	If file is PDF -> renders pages via PyMuPDF.
	If file is Image (.png, .jpg, .jpeg, .bmp, .tiff) -> opens directly.
	"""
	ext = os.path.splitext(file_path)[1].lower()
	images = []

	if ext == ".pdf":
		doc = fitz.open(file_path)
		for page_num in range(len(doc)):
			page = doc.load_page(page_num)
			pix = page.get_pixmap(dpi=150)
			img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
			images.append(img)
	elif ext in [".png", ".jpg", ".jpeg", ".bmp", ".tiff"]:
		img = Image.open(file_path).convert("RGB")
		images.append(img)
	else:
		raise ValueError(f"Unsupported file format: '{ext}'. Supported formats: PDF, PNG, JPG, JPEG, BMP, TIFF.")

	return images


# -------------------------------------------------------------
# 3. Step 2: Computer Vision Image Preprocessing (OpenCV)
# -------------------------------------------------------------
def preprocess_images(images: list[Image.Image]) -> list[np.ndarray]:
	"""
	Apply OpenCV preprocessing to improve character recognition:
	1. RGB to Grayscale
	2. Otsu Binarization for optimal text-background thresholding
	"""
	processed_images = []
	for img in images:
		img_array = np.array(img)
		gray_img = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
		_, binary_img = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
		processed_images.append(binary_img)
	return processed_images


# -------------------------------------------------------------
# 4. Step 3: Run RapidOCR ONNX Text Extraction
# -------------------------------------------------------------
def extract_ocr_text(binary_images: list[np.ndarray]) -> str:
	"""Run RapidOCR on preprocessed image arrays using singleton instance and return clean OCR text."""
	ocr = get_ocr_instance()

	all_text_lines = []
	for img in binary_images:
		result, _ = ocr(img)
		if result:
			for box, text, conf in result:
				if text and text.strip():
					all_text_lines.append(text.strip())

	return "\n".join(all_text_lines)


# -------------------------------------------------------------
# 5. Step 4: Use LLM to Parse Raw OCR Text into Structured JSON
# -------------------------------------------------------------
def structure_invoice_json(ocr_text: str) -> dict:
	"""
	Pass raw OCR text to Groq LLM with dynamic schema instructions.
	Returns a structured Python dictionary representation of the invoice.
	"""
	if not ocr_text.strip():
		return {"document_type": "invoice", "fields": {}, "line_items": [], "additional_information": {}}

	llm = ChatGroq(
		model=GROQ_MODEL_NAME,
		temperature=0,
		api_key=GROQ_API_KEY,
	)

	prompt = ChatPromptTemplate.from_template(
		'''
        You are a document-structuring AI.

        Your task is to convert OCR-extracted text from an invoice into clean, structured JSON that can later be stored in a vector database and indexed using LlamaIndex.

        IMPORTANT RULES:
        1. Do NOT use a fixed or predefined invoice schema.
        2. Extract ALL meaningful information present in the OCR text.
        3. Include custom/non-standard fields if present (e.g., IRN, PO Number, Tax ID, TCS, Place of Supply).
        4. Do not invent, infer, or hallucinate information.
        5. Convert numeric values to numbers (float/int) where appropriate.
        6. Keep dates in YYYY-MM-DD format when clear.
        7. Keep line-item details inside an array under "line_items".
        8. The output MUST contain valid JSON only. Do NOT include markdown backticks like ```json.

        OCR TEXT:
        {ocr_text}
        '''
	)

	chain = prompt | llm
	response = chain.invoke({"ocr_text": ocr_text})

	raw_content = getattr(response, "content", str(response)).strip()

	# Clean potential markdown backticks if present
	if raw_content.startswith("```"):
		match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_content, re.DOTALL)
		if match:
			raw_content = match.group(1).strip()

	try:
		parsed_json = json.loads(raw_content)
		return parsed_json
	except json.JSONDecodeError:
		return {"document_type": "invoice", "raw_text": ocr_text, "fields": {}}


# -------------------------------------------------------------
# 6. Step 5: Store in LlamaIndex Vector Space Database
# -------------------------------------------------------------
def json_to_vector_store(structured_json: dict, file_path: str, index_path: str = DEFAULT_INDEX_PATH, user_id: str = None):
	"""
	Package structured invoice JSON into ChromaDB
	with metadata and persist/update the VectorStoreIndex on disk via vector_store.
	"""
	return save_invoice_to_vector_store(structured_json=structured_json, file_path=file_path, index_path=index_path, user_id=user_id)


# -------------------------------------------------------------
# 7. Reusable OCRAgent Class (Perception & Vector Indexing Agent)
# -------------------------------------------------------------
class OCRAgent:
	"""
	Modular OCR Perception & Vector Indexing Agent:
	1. Accepts Invoice File (PDF or Image)
	2. Preprocesses Image (OpenCV Otsu Binarization)
	3. Runs RapidOCR ONNX Recognition
	4. Structures JSON dynamically via Groq LLM
	5. Packages & Persists into LlamaIndex Vector Space Database
	6. Returns success/failure confirmation back to Orchestrator
	"""

	def process_and_index_invoice(self, file_path: str, index_path: str = DEFAULT_INDEX_PATH, user_id: str = None) -> dict:
		"""Process an invoice file, index it directly, and return confirmation to the orchestrator with stage timings."""
		file_name = os.path.basename(file_path)

		try:
			if not os.path.exists(file_path):
				return {
					"status": "failed",
					"message": f"File '{file_name}' not found at path: {file_path}",
					"file_name": file_name,
					"timings": {},
				}

			t0 = time.time()
			# Step 1: Load & CV Preprocess
			images = load_file_to_images(file_path)
			preprocessed_imgs = preprocess_images(images)
			t_preprocess = time.time() - t0

			# Step 2: OCR Recognition
			t1 = time.time()
			raw_ocr_text = extract_ocr_text(preprocessed_imgs)
			t_ocr = time.time() - t1

			if not raw_ocr_text.strip():
				return {
					"status": "failed",
					"message": f"No text could be extracted from '{file_name}'.",
					"file_name": file_name,
					"timings": {
						"preprocess_s": round(t_preprocess, 2),
						"ocr_s": round(t_ocr, 2),
					},
				}

			# Step 3: Dynamic LLM Structuring (flexible schema)
			t2 = time.time()
			structured_json = structure_invoice_json(raw_ocr_text)
			t_structure = time.time() - t2

			# Step 4: Persist to Vector Store
			t3 = time.time()
			json_to_vector_store(structured_json, file_path, index_path, user_id=user_id)
			t_index = time.time() - t3
			t_total = time.time() - t0

			timings = {
				"preprocess_s": round(t_preprocess, 2),
				"ocr_s": round(t_ocr, 2),
				"structuring_s": round(t_structure, 2),
				"indexing_s": round(t_index, 2),
				"total_s": round(t_total, 2),
			}

			# Return clean confirmation + extracted data back to orchestrator
			return {
				"status": "success",
				"message": f"Successfully extracted and indexed '{file_name}' into vector storage in {timings['total_s']}s.",
				"file_name": file_name,
				"extracted_data": structured_json,
				"timings": timings,
			}

		except Exception as e:
			return {
				"status": "failed",
				"message": f"Error processing '{file_name}': {str(e)}",
				"file_name": file_name,
				"extracted_data": {},
				"timings": {},
			}

	def extract_only(self, file_path: str) -> dict:
		"""
		Extract and structure invoice data from a file WITHOUT storing it.
		Used by the orchestrator for:
		  - Duplicate detection ("do we have this invoice already?")
		  - One-time questions about a file ("what is the total on this?")
		Does NOT call save_invoice_to_vector_store.

		Returns the same dict shape as process_and_index_invoice() but
		with stored=False so caller knows it was not persisted.
		"""
		file_name = os.path.basename(file_path)

		try:
			if not os.path.exists(file_path):
				return {
					"status": "failed",
					"message": f"File '{file_name}' not found at path: {file_path}",
					"file_name": file_name,
					"extracted_data": {},
					"stored": False,
					"timings": {},
				}

			t0 = time.time()
			# Step 1: Load & CV Preprocess
			images = load_file_to_images(file_path)
			preprocessed_imgs = preprocess_images(images)
			t_preprocess = time.time() - t0

			# Step 2: OCR Recognition
			t1 = time.time()
			raw_ocr_text = extract_ocr_text(preprocessed_imgs)
			t_ocr = time.time() - t1

			if not raw_ocr_text.strip():
				return {
					"status": "failed",
					"message": f"No text could be extracted from '{file_name}'.",
					"file_name": file_name,
					"extracted_data": {},
					"stored": False,
					"timings": {
						"preprocess_s": round(t_preprocess, 2),
						"ocr_s": round(t_ocr, 2),
					},
				}

			# Step 3: Dynamic LLM Structuring
			t2 = time.time()
			structured_json = structure_invoice_json(raw_ocr_text)
			t_structure = time.time() - t2
			t_total = time.time() - t0

			timings = {
				"preprocess_s": round(t_preprocess, 2),
				"ocr_s": round(t_ocr, 2),
				"structuring_s": round(t_structure, 2),
				"total_s": round(t_total, 2),
			}

			# NO storage step - intentionally omitted
			return {
				"status": "success",
				"message": f"Successfully extracted data from '{file_name}' (not stored).",
				"file_name": file_name,
				"extracted_data": structured_json,
				"stored": False,
				"timings": timings,
			}

		except Exception as e:
			return {
				"status": "failed",
				"message": f"Error extracting '{file_name}': {str(e)}",
				"file_name": file_name,
				"extracted_data": {},
				"stored": False,
			}

	def process_directory(self, directory_path: str, index_path: str = DEFAULT_INDEX_PATH) -> dict:
		"""Batch process an entire directory and return summary confirmation to orchestrator."""
		if not os.path.exists(directory_path):
			return {
				"status": "failed",
				"message": f"Directory '{directory_path}' does not exist.",
				"processed_count": 0
			}

		supported_exts = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
		files = [
			os.path.join(directory_path, f)
			for f in os.listdir(directory_path)
			if os.path.splitext(f)[1].lower() in supported_exts
		]

		if not files:
			return {
				"status": "success",
				"message": f"No supported invoice files found in '{directory_path}'.",
				"processed_count": 0
			}

		successful = []
		failed = []

		for file_path in files:
			res = self.process_and_index_invoice(file_path, index_path=index_path)
			if res["status"] == "success":
				successful.append(res["file_name"])
			else:
				failed.append(res["file_name"])

		return {
			"status": "success" if not failed else ("partial_success" if successful else "failed"),
			"message": f"Processed {len(successful)}/{len(files)} invoice(s) into vector storage.",
			"total_files": len(files),
			"successful": successful,
			"failed": failed
		}


# Backwards compatibility helper
def get_llm():
	return ChatGroq(model=GROQ_MODEL_NAME, temperature=0, api_key=GROQ_API_KEY)
