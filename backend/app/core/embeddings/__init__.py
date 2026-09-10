"""
Aegis — Embedding Models

Backs the Marketplace's Embedding Models category and the workflow
"vector" node's optional embeddingModel choice (see
app.core.workflows.engine._run_vector_node). registry.py wraps fastembed's
own supported-model list; manager.py resolves a chosen (or default) model
id to a ready fastembed.TextEmbedding instance, cached per process.

Dense text models only — fastembed also lists sparse/late-interaction/
image models under other classes, which this app's RAG pipeline and vector
nodes don't use and this package doesn't surface.
"""
