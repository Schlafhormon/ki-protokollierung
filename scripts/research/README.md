# Research Scripts

This directory contains non-production prototypes that were useful while exploring transcript segmentation and protocol generation. They are kept separate from the runtime scripts because several of them contain local sample paths, depend on ad-hoc input files, or target external research services.

## Layout

- `segment_transcript.py` uses semantic embeddings to segment a transcript by agenda topics.
- `extract_moderator_transcript.py` extracts moderator utterances from a transcript.
- `moderator_segmentation.py` and `moderator_segmentation_70B.py` test moderator-based segmentation via LLMs.
- `minutes_generator.py` generates draft minutes from precomputed boundaries.
- `llama-70b/` contains an experimental Kubernetes deployment for a 70B model.
- `archive/` contains older protocol-generation experiments kept only for reference.

## Notes

These scripts are not wired into the FastAPI/React application, Docker Compose setup, or Kubernetes manifests. Before running one, replace hard-coded local sample paths in its `main()` block with local inputs or refactor it to accept CLI arguments.

Generated transcripts, boundaries, protocol drafts, and local data should stay untracked; the repository root `.gitignore` contains patterns for the common outputs.
