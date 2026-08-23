Standardize file-hub LLM usage on the fleet robotsix-llmio pattern:

- Chat enrichment (summary/category/tags) goes through **robotsix-llmio** (OpenRouter transport) with a configurable tier level (default 1), emits Langfuse traces via the canonical `langfuse` + `openrouter` config blocks.
- Embeddings stay on a direct `httpx` call but point at the shared self-hosted `bge-m3` embedding server via the new dedicated `embedding` config block.
- Removed legacy `enrichment_llm_api_base`, `enrichment_llm_api_key`, `enrichment_llm_model`, `enrichment_llm_max_tokens`, `enrichment_llm_timeout`, and `enrichment_llm_embedding_model` fields.