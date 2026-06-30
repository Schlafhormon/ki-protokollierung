#!/usr/bin/env python3
"""
Meeting Transcript Segmentation System
Segments long meeting transcripts by agenda topics using semantic embeddings
"""

import re
import json
from typing import List, Dict, Tuple
from pathlib import Path
from collections import Counter

import numpy as np
from scipy.ndimage import median_filter
from sentence_transformers import SentenceTransformer, util
import PyPDF2


class TranscriptSegmenter:
    """Segments meeting transcripts by agenda topics"""

    def __init__(self, model_name: str = "paraphrase-multilingual-mpnet-base-v2"):
        """
        Initialize the segmenter

        Args:
            model_name: HuggingFace model for multilingual embeddings
        """
        print(f"Loading embedding model: {model_name}...")
        self.model = SentenceTransformer(model_name)
        print("Model loaded successfully!")

    def extract_topics_from_pdf(self, pdf_path: str) -> List[str]:
        """
        Extract agenda topics (TOPs) from meeting invitation PDF

        Args:
            pdf_path: Path to the PDF file

        Returns:
            List of topic strings
        """
        print(f"\nExtracting topics from {pdf_path}...")

        with open(pdf_path, "rb") as file:
            pdf_reader = PyPDF2.PdfReader(file)
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text()

        # Extract numbered topics from agenda
        # Pattern matches: "4. Topic description" or "4 Topic description"
        pattern = r"(\d+)\.\s+(.+?)(?=\n\d+\.|\n\n|$)"
        matches = re.findall(pattern, text, re.DOTALL)

        topics = []
        for num, topic in matches:
            # Clean up topic text
            topic = topic.strip()
            topic = re.sub(r"\s+", " ", topic)  # Replace multiple spaces
            topic = re.sub(r"\n", " ", topic)  # Replace newlines

            # Skip very short or procedural items
            if len(topic) > 15 and not any(
                skip in topic.lower()
                for skip in ["feststellung", "bestätigung", "protokoll", "tagesordnung"]
            ):
                topics.append(f"{num}. {topic}")

        print(f"Found {len(topics)} main topics:")
        for i, topic in enumerate(topics, 1):
            print(f"  {i}. {topic[:80]}..." if len(topic) > 80 else f"  {i}. {topic}")

        return topics

    def load_transcript(self, transcript_path: str) -> List[Dict[str, str]]:
        """
        Load and parse transcript file

        Args:
            transcript_path: Path to transcript text file

        Returns:
            List of dicts with speaker and text
        """
        print(f"\nLoading transcript from {transcript_path}...")

        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Parse speaker lines: [SPEAKER_XX]: text
        pattern = r"\[SPEAKER_(\d+)\]:\s*(.+)"

        utterances = []
        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = re.match(pattern, line)
            if match:
                speaker_id = match.group(1)
                text = match.group(2).strip()
                if text:  # Skip empty utterances
                    utterances.append(
                        {"speaker": f"SPEAKER_{speaker_id}", "text": text}
                    )

        print(
            f"Loaded {len(utterances)} utterances from {len(set(u['speaker'] for u in utterances))} speakers"
        )
        return utterances

    def create_chunks(
        self, utterances: List[Dict[str, str]], chunk_size: int = 5, overlap: int = 1
    ) -> List[Dict]:
        """
        Create overlapping chunks from utterances for better context

        Args:
            utterances: List of utterance dicts
            chunk_size: Number of utterances per chunk
            overlap: Number of overlapping utterances between chunks

        Returns:
            List of chunk dicts with text and metadata
        """
        print(f"\nCreating chunks (size={chunk_size}, overlap={overlap})...")

        chunks = []
        step = chunk_size - overlap

        for i in range(0, len(utterances), step):
            chunk_utterances = utterances[i : i + chunk_size]

            if not chunk_utterances:
                continue

            # Combine text from all utterances in chunk
            chunk_text = " ".join(
                [f"{u['speaker']}: {u['text']}" for u in chunk_utterances]
            )

            chunks.append(
                {
                    "text": chunk_text,
                    "start_idx": i,
                    "end_idx": min(i + chunk_size, len(utterances)),
                    "speakers": list(set(u["speaker"] for u in chunk_utterances)),
                }
            )

        print(f"Created {len(chunks)} chunks")
        return chunks

    def assign_topics(
        self, chunks: List[Dict], topics: List[str], similarity_threshold: float = 0.3
    ) -> List[int]:
        """
        Assign topic labels to chunks using embeddings

        Args:
            chunks: List of chunk dicts
            topics: List of topic strings
            similarity_threshold: Minimum similarity to assign topic

        Returns:
            List of topic indices for each chunk
        """
        print(f"\nAssigning topics to chunks...")

        # Embed topics
        print("Embedding topics...")
        topic_embeddings = self.model.encode(topics, convert_to_tensor=True)

        # Embed chunks
        print("Embedding chunks...")
        chunk_texts = [c["text"] for c in chunks]
        chunk_embeddings = self.model.encode(chunk_texts, convert_to_tensor=True)

        # Calculate similarities
        print("Calculating similarities...")
        similarities = util.cos_sim(chunk_embeddings, topic_embeddings)

        # Assign topics
        assignments = []
        for i, sim_scores in enumerate(similarities):
            max_score = sim_scores.max().item()
            best_topic = sim_scores.argmax().item()

            # If similarity too low, assign to previous topic if exists
            if max_score < similarity_threshold and i > 0:
                assignments.append(assignments[-1])
            else:
                assignments.append(best_topic)

        print(f"Initial assignment complete")
        return assignments

    def smooth_assignments(
        self, assignments: List[int], window_size: int = 3, min_segment_length: int = 2
    ) -> List[int]:
        """
        Apply temporal smoothing to reduce topic jumping

        Args:
            assignments: Raw topic assignments
            window_size: Size of median filter window
            min_segment_length: Minimum consecutive chunks for a topic

        Returns:
            Smoothed topic assignments
        """
        print(f"\nApplying temporal smoothing (window={window_size})...")

        # Apply median filter
        smoothed = median_filter(assignments, size=window_size, mode="nearest")
        smoothed = smoothed.astype(int).tolist()

        # Enforce minimum segment length
        final = []
        i = 0
        while i < len(smoothed):
            current_topic = smoothed[i]
            segment_length = 1

            # Count consecutive same topic
            while (
                i + segment_length < len(smoothed)
                and smoothed[i + segment_length] == current_topic
            ):
                segment_length += 1

            # If segment too short, merge with previous or next
            if segment_length < min_segment_length and final:
                # Extend previous topic
                final.extend([final[-1]] * segment_length)
            else:
                final.extend([current_topic] * segment_length)

            i += segment_length

        # Calculate improvement
        changes_before = sum(
            1
            for i in range(len(assignments) - 1)
            if assignments[i] != assignments[i + 1]
        )
        changes_after = sum(
            1 for i in range(len(final) - 1) if final[i] != final[i + 1]
        )
        print(f"Reduced topic transitions from {changes_before} to {changes_after}")

        return final

    def generate_output(
        self,
        utterances: List[Dict],
        chunks: List[Dict],
        assignments: List[int],
        topics: List[str],
        output_path: str,
    ):
        """
        Generate segmented output file

        Args:
            utterances: Original utterances
            chunks: Chunk metadata
            assignments: Topic assignments per chunk
            topics: List of topic strings
            output_path: Path for output file
        """
        print(f"\nGenerating output to {output_path}...")

        # Map utterance indices to topics
        utterance_topics = [None] * len(utterances)
        for chunk, topic_idx in zip(chunks, assignments):
            for i in range(chunk["start_idx"], chunk["end_idx"]):
                if i < len(utterance_topics):
                    utterance_topics[i] = topic_idx

        # Write segmented output
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write("SEGMENTED MEETING TRANSCRIPT\n")
            f.write("=" * 80 + "\n\n")

            current_topic = None
            utterance_count = 0

            for i, (utterance, topic_idx) in enumerate(
                zip(utterances, utterance_topics)
            ):
                # New topic section
                if topic_idx != current_topic:
                    if current_topic is not None:
                        f.write(f"\n[End of topic - {utterance_count} utterances]\n")
                        f.write("-" * 80 + "\n\n")

                    current_topic = topic_idx
                    utterance_count = 0

                    topic_name = (
                        topics[topic_idx] if topic_idx is not None else "Unknown Topic"
                    )
                    f.write("\n" + "=" * 80 + "\n")
                    f.write(f"TOPIC: {topic_name}\n")
                    f.write("=" * 80 + "\n\n")

                # Write utterance
                f.write(f"[{utterance['speaker']}]: {utterance['text']}\n")
                utterance_count += 1

            # Final topic end
            if current_topic is not None:
                f.write(f"\n[End of topic - {utterance_count} utterances]\n")
                f.write("-" * 80 + "\n")

        print(f"Output written successfully!")

        # Print statistics
        print("\n" + "=" * 80)
        print("SEGMENTATION STATISTICS")
        print("=" * 80)
        topic_counts = Counter(assignments)
        for topic_idx, count in sorted(topic_counts.items()):
            topic_name = topics[topic_idx]
            percentage = (count / len(assignments)) * 100
            print(f"{topic_name[:60]:60s} {count:3d} chunks ({percentage:5.1f}%)")

    def segment_transcript(
        self,
        agenda_pdf: str,
        transcript_file: str,
        output_file: str,
        chunk_size: int = 5,
        chunk_overlap: int = 1,
        similarity_threshold: float = 0.3,
        smooth_window: int = 3,
        min_segment_length: int = 2,
    ):
        """
        Complete pipeline to segment transcript

        Args:
            agenda_pdf: Path to agenda PDF
            transcript_file: Path to transcript text file
            output_file: Path for output file
            chunk_size: Utterances per chunk (5-7 recommended)
            chunk_overlap: Overlap between chunks (1-2 recommended)
            similarity_threshold: Min similarity for topic assignment (0.2-0.4)
            smooth_window: Median filter window (3-5 recommended)
            min_segment_length: Min chunks per topic (2-3 recommended)
        """
        print("\n" + "=" * 80)
        print("MEETING TRANSCRIPT SEGMENTATION PIPELINE")
        print("=" * 80)

        # Step 1: Extract topics from agenda
        topics = self.extract_topics_from_pdf(agenda_pdf)

        if not topics:
            print("ERROR: No topics found in agenda PDF!")
            return

        # Step 2: Load transcript
        utterances = self.load_transcript(transcript_file)

        if not utterances:
            print("ERROR: No utterances found in transcript!")
            return

        # Step 3: Create chunks
        chunks = self.create_chunks(utterances, chunk_size, chunk_overlap)

        # Step 4: Assign topics
        assignments = self.assign_topics(chunks, topics, similarity_threshold)

        # Step 5: Smooth assignments
        smoothed_assignments = self.smooth_assignments(
            assignments, smooth_window, min_segment_length
        )

        # Step 6: Generate output
        self.generate_output(
            utterances, chunks, smoothed_assignments, topics, output_file
        )

        print("\n" + "=" * 80)
        print("PIPELINE COMPLETE!")
        print("=" * 80)


def main():
    """Main entry point"""

    # Initialize segmenter
    segmenter = TranscriptSegmenter(model_name="paraphrase-multilingual-mpnet-base-v2")

    # Configure paths
    agenda_pdf = "data/Einladung_KA_10_09_2025.pdf"
    transcript_file = "data/20250910_Sondersitzung_KA.txt"
    output_file = "data/KA_segmented_transcript.txt"

    # Run segmentation pipeline with optimal parameters
    segmenter.segment_transcript(
        agenda_pdf=agenda_pdf,
        transcript_file=transcript_file,
        output_file=output_file,
        chunk_size=5,  # 5 utterances per chunk (good for conversation flow)
        chunk_overlap=1,  # 1 utterance overlap (smooth transitions)
        similarity_threshold=0.3,  # 0.3 balanced threshold (not too strict/loose)
        smooth_window=3,  # 3-chunk median filter (removes noise)
        min_segment_length=2,  # Min 2 chunks per topic (filters tiny segments)
    )

    print(f"\n✓ Segmented transcript saved to: {output_file}")


if __name__ == "__main__":
    main()
