#!/usr/bin/env python3
"""
LLM-Based Meeting Transcript Summarisation
Uses Qwen3 LLM to intelligently summarise transcripts based on TOPs extracted from agenda PDFs
"""

import re
import json
from typing import List, Dict, Tuple
from pathlib import Path

import PyPDF2
import requests


class LLMTranscriptSummariser:
    """Summarises meeting transcripts using LLM to detect topic boundaries"""

    def __init__(self,
                 ollama_model: str = "qwen3:8b",
                 ollama_url: str = "http://localhost:11434"):
        """
        Initialize the LLM-based summariser

        Args:
            ollama_model: Ollama model name (default: qwen3:8b)
            ollama_url: Ollama API URL (default: http://localhost:11434)
        """
        self.ollama_model = ollama_model
        self.ollama_url = ollama_url
        print(f"Initialized LLM Summariser with model: {ollama_model}")

    def extract_topics_from_pdf(self, pdf_path: str) -> List[str]:
        """
        Extract agenda topics using Qwen3 via Ollama

        Args:
            pdf_path: Path to the PDF file

        Returns:
            List of topic strings

        Raises:
            Exception if extraction fails
        """
        print(f"\nExtracting topics with Qwen3 from {pdf_path}...")

        # Extract text from PDF
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text()
        except Exception as e:
            raise Exception(f"Error reading PDF: {e}")

        # Prepare prompt for Qwen3 with few-shot example
        prompt = f"""Extract ALL agenda items (Tagesordnungspunkte) from German meeting documents.

EXAMPLE INPUT:
I. Öffentlicher Teil:
1. Feststellung der Beschlussfähigkeit
2. Bestätigung der Tagesordnung
3. Haushaltsplan 2026

II. Nichtöffentlicher Teil:
1. Bestätigung der Niederschrift
2. Bauvorhaben
   2.1. Neubau Schule Erkner
   2.2. Sanierung Rathaus

EXPECTED OUTPUT:
["1. Feststellung der Beschlussfähigkeit", "2. Bestätigung der Tagesordnung", "3. Haushaltsplan 2026", "1. Bestätigung der Niederschrift", "2.1. Neubau Schule Erkner", "2.2. Sanierung Rathaus"]

RULES:
- Extract from BOTH Öffentlicher and Nichtöffentlicher sections
- Extract ALL items (including procedural ones like Feststellung, Bestätigung)
- Extract ONLY leaf nodes (if topic has sub-items like 2.1, 2.2, skip the parent topic 2)
- Keep standalone topics that have no children
- Keep original numbering and full descriptions
- Return flat JSON array (not nested object)

NOW EXTRACT FROM THIS DOCUMENT:
{text}

Return ONLY the JSON array:"""

        # Call Ollama API
        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.1,
                    }
                },
                timeout=120
            )
            response.raise_for_status()

            result = response.json()
            response_text = result.get('response', '')

            # Parse JSON response
            topics = json.loads(response_text)

            print("Topics:", topics)

            # Handle if LLM wrapped array in object
            if isinstance(topics, dict):
                for key in ['items', 'topics', 'tagesordnungspunkte', 'list', 'agenda', "agenda_items", "results"]:
                    if key in topics and isinstance(topics[key], list):
                        topics = topics[key]
                        print(f"Unwrapped array from '{key}' field")
                        break

            # Validate it's a list
            if not isinstance(topics, list):
                raise Exception("LLM returned non-list response")

            # Filter and clean topics
            cleaned_topics = []
            for topic in topics:
                if isinstance(topic, str) and len(topic.strip()) > 0:
                    cleaned_topics.append(topic.strip())

            if not cleaned_topics:
                raise Exception("No valid topics extracted by LLM")

            print(f"✓ LLM extracted {len(cleaned_topics)} topics:")
            for i, topic in enumerate(cleaned_topics, 1):
                print(f"  {i}. {topic[:80]}..." if len(topic) > 80 else f"  {i}. {topic}")

            return cleaned_topics

        except requests.exceptions.RequestException as e:
            raise Exception(f"Error calling Ollama API: {e}. Make sure Ollama is running: ollama serve")
        except json.JSONDecodeError as e:
            raise Exception(f"Error parsing LLM response as JSON: {e}")
        except Exception as e:
            if "No valid topics" in str(e) or "non-list" in str(e):
                raise
            raise Exception(f"Unexpected error in LLM extraction: {e}")

    def load_transcript(self, transcript_path: str) -> List[Dict[str, str]]:
        """
        Load and parse transcript file

        Args:
            transcript_path: Path to transcript text file

        Returns:
            List of dicts with line_num, speaker, and text
        """
        print(f"\nLoading transcript from {transcript_path}...")

        with open(transcript_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Parse speaker lines: [SPEAKER_XX]: text
        pattern = r'\[SPEAKER_(\d+)\]:\s*(.+)'

        utterances = []
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue

            match = re.match(pattern, line)
            if match:
                speaker_id = match.group(1)
                text = match.group(2).strip()
                if text:
                    utterances.append({
                        'line_num': line_num,
                        'speaker': f"SPEAKER_{speaker_id}",
                        'text': text
                    })

        print(f"Loaded {len(utterances)} utterances from {len(set(u['speaker'] for u in utterances))} speakers")
        return utterances


    def summarise_transcript(self,
                          agenda_pdf: str,
                          transcript_file: str,
                          output_file: str):
        """
        Complete LLM-based summarisation pipeline

        Args:
            agenda_pdf: Path to agenda PDF
            transcript_file: Path to transcript text file
            output_file: Path for output file
        """
        print("\n" + "=" * 80)
        print("LLM-BASED TRANSCRIPT SUMMARISATION PIPELINE")
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

        print("\n" + "=" * 80)
        print("PIPELINE COMPLETE!")
        print("=" * 80)


def main():
    """Main entry point"""

    # Initialize LLM-based summariser
    summariser = LLMTranscriptSummariser()

    # Configure paths
    agenda_pdf = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250910_KA/Einladung_KA_10_09_2025.pdf"
    transcript_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250910_KA/20250910_Sondersitzung_KA.txt"
    output_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250910_KA/llm_summarised_transcript.txt"

    # Run LLM-based summarisation pipeline
    summariser.summarise_transcript(
        agenda_pdf=agenda_pdf,
        transcript_file=transcript_file,
        output_file=output_file
    )

    print(f"\n✓ LLM-summarised transcript saved to: {output_file}")


if __name__ == "__main__":
    main()
