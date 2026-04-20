import os
import pandas as pd
from typing import List, Optional
from docling.document_converter import DocumentConverter
from modules.utils import logger

class DoclingParser:
    """
    High-fidelity document parser using IBM's Docling.
    Specializes in table extraction and layout-aware text conversion.
    """
    def __init__(self):
        self.converter = DocumentConverter()

    def parse_to_markdown(self, file_path: str) -> str:
        """Converts document to clean, layout-aware Markdown."""
        try:
            result = self.converter.convert(file_path)
            return result.document.export_to_markdown()
        except Exception as e:
            logger.error(f"Docling conversion failed for {file_path}: {e}")
            return ""

    def extract_tables(self, file_path: str) -> List[pd.DataFrame]:
        """Extracts all tables from a document as Pandas DataFrames."""
        try:
            result = self.converter.convert(file_path)
            tables = []
            
            # Docling stores tables in its document structure
            # We iterate through the document elements to find tables
            for element, level in result.document.iterate_items():
                if hasattr(element, "export_to_dataframe"):
                    df = element.export_to_dataframe()
                    if df is not None and not df.empty:
                        tables.append(df)
            
            return tables
        except Exception as e:
            logger.error(f"Docling table extraction failed for {file_path}: {e}")
            return []

# Helper singleton or factory
_PARSER = None

def get_docling_parser():
    global _PARSER
    if _PARSER is None:
        _PARSER = DoclingParser()
    return _PARSER

def extract_tables_with_docling(file_path: str) -> List[pd.DataFrame]:
    """Utility function for quick table extraction."""
    parser = get_docling_parser()
    return parser.extract_tables(file_path)

def convert_to_markdown(file_path: str) -> str:
    """Utility function for quick markdown conversion."""
    parser = get_docling_parser()
    return parser.parse_to_markdown(file_path)
