import os
from typing import List, Dict, Any
from lightrag import LightRAG, QueryParam
from modules.utils import logger, DATA_ROOT

class ProRAG:
    """
    Advanced RAG system using LightRAG (Knowledge Graph + Vector).
    Ideal for complex procurement rules and relationship-aware queries.
    """
    def __init__(self, kb_dir: str = None):
        self.kb_dir = kb_dir or str(DATA_ROOT / "knowledge_base_pro")
        os.makedirs(self.kb_dir, exist_ok=True)
        
        # Initialize LightRAG
        # Note: LightRAG usually requires an LLM function. 
        # We wrap our Gemini/Groq calls from utils.
        from modules.utils import ask_gemini
        
        self.rag = LightRAG(
            working_dir=self.kb_dir,
            llm_model_func=self._llm_wrapper
        )

    async def _llm_wrapper(self, prompt: str, **kwargs) -> str:
        """Bridge between LightRAG and our existing Gemini utility."""
        from modules.utils import ask_gemini
        return ask_gemini(prompt)

    def index_document(self, text: str):
        """Incrementally index new document text into the Knowledge Graph."""
        try:
            self.rag.insert(text)
            logger.info("Document indexed successfully in ProRAG.")
        except Exception as e:
            logger.error(f"ProRAG indexing failed: {e}")

    def query(self, question: str, mode: str = "hybrid") -> Dict[str, Any]:
        """
        Query the RAG system.
        Modes: 'local' (granular), 'global' (conceptual), 'hybrid' (both).
        """
        try:
            # LightRAG query param
            param = QueryParam(mode=mode)
            answer = self.rag.query(question, param=param)
            
            return {
                "answer": answer,
                "sources": ["Knowledge Graph", "Vector Index"],
                "mode": mode
            }
        except Exception as e:
            logger.error(f"ProRAG query failed: {e}")
            return {"answer": f"Error: {e}", "sources": []}

# Singleton helper
_PRO_RAG = None

def get_pro_rag():
    global _PRO_RAG
    if _PRO_RAG is None:
        _PRO_RAG = ProRAG()
    return _PRO_RAG

def ask_pro_rag(question: str, mode: str = "hybrid") -> Dict[str, Any]:
    """Utility for dashboard integration."""
    return get_pro_rag().query(question, mode=mode)
