#!/usr/bin/env python3
"""
Semantic LLM-Based Meeting Protocol Generator
Uses semantic search to match transcript content to agenda TOPs (Tagesordnungspunkte)
"""

import re
import json
import os
from typing import List, Dict, Set

import PyPDF2
import requests


class LLMProtocolGenerator:
    """Generates meeting protocols using semantic matching of transcript to agenda TOPs"""

    def __init__(self,
                 ollama_model: str = "gemma3:27b",
                 ollama_url: str = "http://localhost:11434",
                 chunk_size: int = 500,
                 chunk_overlap: int = 50):
        """
        Initialize the protocol generator

        Args:
            ollama_model: Ollama model name
            ollama_url: Ollama API URL (default: http://localhost:11434)
            chunk_size: Number of utterances per chunk for long transcripts
            chunk_overlap: Overlap between chunks to avoid missing context
        """
        self.ollama_model = ollama_model
        self.ollama_url = ollama_url
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        print(f"Initialized Protocol Generator with model: {ollama_model}")
        print(f"Chunk settings: size={chunk_size}, overlap={chunk_overlap}")

    def load_topics_from_file(self, topics_file: str) -> List[str]:
        """
        Load topics from a text file

        Args:
            topics_file: Path to topics text file (one topic per line)

        Returns:
            List of topic strings
        """
        print(f"\nLoading topics from {topics_file}...")
        with open(topics_file, 'r', encoding='utf-8') as f:
            topics = [line.strip() for line in f if line.strip()]

        print(f"✓ Loaded {len(topics)} topics from file:")
        for i, topic in enumerate(topics, 1):
            print(f"  {i}. {topic[:80]}..." if len(topic) > 80 else f"  {i}. {topic}")

        return topics

    def save_topics_to_file(self, topics: List[str], topics_file: str):
        """
        Save topics to a text file

        Args:
            topics: List of topic strings
            topics_file: Path to output file
        """
        with open(topics_file, 'w', encoding='utf-8') as f:
            for topic in topics:
                f.write(topic + '\n')
        print(f"✓ Saved {len(topics)} topics to: {topics_file}")

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
        prompt = f"""Extract ALL agenda items (Tagesordnungspunkte or TOPs) from German meeting documents.

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
["1. Feststellung der Beschlussfähigkeit", "2. Bestätigung der Tagesordnung", "3. Haushaltsplan 2026", "1. Bestätigung der Niederschrift", "2. Bauvorhaben - 2.1. Neubau Schule Erkner", "2. Bauvorhaben - 2.2. Sanierung Rathaus"]

RULES:
- Extract from BOTH Öffentlicher and Nichtöffentlicher sections
- Extract ALL items (including procedural ones like Feststellung, Bestätigung)
- For items with sub-items (like 2.1, 2.2 under parent 2), combine parent + subpoint: "2. ParentTitle - 2.1. SubpointTitle"
- Keep standalone topics that have no children as-is
- Keep original numbering and full descriptions
- Return flat JSON array (not nested object)
- Parent context is IMPORTANT for semantic understanding

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
                    "options": {
                        "temperature": 0.1,
                    }
                },
                timeout=120
            )
            response.raise_for_status()

            result = response.json()
            response_text = result.get('response', '').strip()

            print(f"Raw LLM response length: {len(response_text)} chars")

            # Try to extract JSON array from response (may have extra text)
            # Look for array pattern [...] in the response
            json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                topics = json.loads(json_str)
            else:
                # Fallback: try parsing whole response
                topics = json.loads(response_text)

            print("Parsed topics:", topics)

            # Handle if LLM wrapped array in object
            if isinstance(topics, dict):
                for key in ['items', 'topics', 'tagesordnungspunkte', 'list', 'agenda', "agenda_items", "results", "Tagesordnungspunkte"]:
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

    def chunk_transcript(self, utterances: List[Dict]) -> List[List[Dict]]:
        """
        Split transcript into overlapping chunks for processing

        Args:
            utterances: List of utterance dicts

        Returns:
            List of chunks (each chunk is a list of utterances)
        """
        if len(utterances) <= self.chunk_size:
            return [utterances]

        chunks = []
        start = 0
        while start < len(utterances):
            end = min(start + self.chunk_size, len(utterances))
            chunks.append(utterances[start:end])

            # Move start forward, accounting for overlap
            start += self.chunk_size - self.chunk_overlap

            # Stop if we've covered everything
            if end == len(utterances):
                break

        print(f"Split transcript into {len(chunks)} chunks")
        return chunks

    def format_chunk_text(self, chunk: List[Dict]) -> str:
        """
        Format a chunk of utterances for LLM processing

        Args:
            chunk: List of utterance dicts

        Returns:
            Formatted text string
        """
        lines = []
        for utt in chunk:
            lines.append(f"Line {utt['line_num']} - [{utt['speaker']}]: {utt['text']}")
        return "\n".join(lines)

    def extract_top_discussion(self,
                               chunk: List[Dict],
                               top: str,
                               chunk_index: int,
                               total_chunks: int) -> Dict:
        """
        Search a transcript chunk for discussion related to a specific TOP

        Args:
            chunk: List of utterances in this chunk
            top: TOP string (e.g., "1. Feststellung der Beschlussfähigkeit")
            chunk_index: Current chunk number (for logging)
            total_chunks: Total number of chunks (for logging)

        Returns:
            Dict with extracted information about this TOP in this chunk
        """
        chunk_text = self.format_chunk_text(chunk)
        top_identifier = top

        prompt = f"""You are analyzing a German municipal meeting transcript to find discussion about a specific agenda item.

EXAMPLES OF WHAT "DISCUSSED" MEANS:

EXAMPLE 1 - DISCUSSED (Direct mention):
TOP: "1. Feststellung der Beschlussfähigkeit"
Transcript snippet:
Line 9 - [SPEAKER_02]: Wir sind zwölf Stimmberechtigte von 15 Abgeordnete hier anwesend.
Line 10 - [SPEAKER_02]: Möchte ich in die Tagesordnung einsteigen und zuerst die Beschlussfähigkeit feststellen.

Result: {{"discussed": true, "relevant_line_numbers": [9, 10], "summary": "Feststellung der Beschlussfähigkeit erfolgte. 12 von 15 Stimmberechtigten anwesend."}}

EXAMPLE 2 - DISCUSSED (Implicit/semantic):
TOP: "2. Bestätigung der Tagesordnung"
Transcript snippet:
Line 14 - [SPEAKER_02]: Gibt es Anmerkungen zur Tagesordnung von Ihrer Seite?
Line 159 - [SPEAKER_02]: Wer mit der so geänderten Tagesordnung für heute einverstanden ist, den bitte ich ums Handzeichen.

Result: {{"discussed": true, "relevant_line_numbers": [14, 156, 159], "summary": "Tagesordnung wurde diskutiert und mit Änderungen beschlossen."}}

EXAMPLE 3 - NOT DISCUSSED:
TOP: "5. Bauvorhaben Schule"
Transcript snippet:
Line 50 - [SPEAKER_02]: Wir kommen zum nächsten Punkt.
Line 51 - [SPEAKER_02]: Gibt es weitere Fragen?

Result: {{"discussed": false, "relevant_line_numbers": []}}

IMPORTANT RULES:
- Mark "discussed": true if the topic is mentioned, debated, voted on, or decided - even indirectly
- Look for semantic relevance (e.g., "Niederschrift" = "Protokoll", "Haushaltsplan" = "Budget")
- If speakers talk about the content/subject of a TOP (even without naming it), it counts as discussed
- Only mark false if the TOP is truly not mentioned or addressed at all

NOW ANALYZE THIS:

AGENDA ITEM (Tagesordnungspunkt):
{top_identifier}

TRANSCRIPT SECTION:
{chunk_text}

TASK:
Extract the following information as JSON:
{{
  "discussed": true or false,
  "relevant_line_numbers": [10, 11, 45],
  "summary": "Brief summary of discussion (2-3 sentences in German)",
  "decisions": ["Entscheidung 1", "Entscheidung 2"],
  "votes": "Description of any votes taken, or null",
  "action_items": ["Maßnahme 1", "Maßnahme 2"],
  "key_speakers": ["SPEAKER_02", "SPEAKER_08"]
}}

If the agenda item is NOT discussed at all in this section, return:
{{
  "discussed": false,
  "relevant_line_numbers": [],
  "summary": null,
  "decisions": [],
  "votes": null,
  "action_items": [],
  "key_speakers": []
}}

Return ONLY valid JSON:"""

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                    }
                },
                timeout=600
            )
            response.raise_for_status()

            result = response.json()
            response_text = result.get('response', '').strip()

            # Try to extract JSON object from response (may have extra text)
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                extracted = json.loads(json_str)
            else:
                # Fallback: try parsing whole response
                extracted = json.loads(response_text)

            # Log progress
            if extracted.get('discussed', False):
                num_lines = len(extracted.get('relevant_line_numbers', []))
                print(f"  ✓ Chunk {chunk_index + 1}/{total_chunks}: Found {num_lines} relevant lines for {top_identifier[:50]}...")
            else:
                print(f"  - Chunk {chunk_index + 1}/{total_chunks}: No discussion of {top_identifier[:50]}...")

            return extracted

        except Exception as e:
            print(f"  ✗ Error processing chunk {chunk_index + 1}/{total_chunks} for TOP: {e}")
            return {
                "discussed": False,
                "relevant_line_numbers": [],
                "summary": None,
                "decisions": [],
                "votes": None,
                "action_items": [],
                "key_speakers": []
            }

    def aggregate_top_results(self, chunk_results: List[Dict], top: str) -> Dict:
        """
        Aggregate results from multiple chunks for a single TOP

        Args:
            chunk_results: List of extraction results from each chunk
            top: The TOP string being processed

        Returns:
            Aggregated result dict
        """
        # Filter only chunks where topic was discussed
        discussed_chunks = [r for r in chunk_results if r.get('discussed', False)]

        if not discussed_chunks:
            return {
                "discussed": False,
                "line_numbers": [],
                "summary": f"Der Tagesordnungspunkt '{top}' wurde in der Sitzung nicht besprochen.",
                "decisions": [],
                "votes": None,
                "action_items": [],
                "key_speakers": []
            }

        # Aggregate line numbers (deduplicate)
        all_lines: Set[int] = set()
        for chunk in discussed_chunks:
            all_lines.update(chunk.get('relevant_line_numbers', []))

        # Aggregate other fields
        all_decisions = []
        all_actions = []
        all_speakers: Set[str] = set()
        summaries = []
        votes = []

        for chunk in discussed_chunks:
            all_decisions.extend(chunk.get('decisions', []))
            all_actions.extend(chunk.get('action_items', []))
            all_speakers.update(chunk.get('key_speakers', []))

            if chunk.get('summary'):
                summaries.append(chunk['summary'])
            if chunk.get('votes'):
                vote_data = chunk['votes']
                # Handle if votes is a dict, list, or string
                if isinstance(vote_data, str):
                    votes.append(vote_data)
                elif isinstance(vote_data, dict):
                    votes.append(str(vote_data))
                elif isinstance(vote_data, list):
                    votes.extend([str(v) for v in vote_data])

        # Combine summaries
        combined_summary = " ".join(summaries) if summaries else "Diskussion erfolgte."

        return {
            "discussed": True,
            "line_numbers": sorted(list(all_lines)),
            "summary": combined_summary,
            "decisions": all_decisions,
            "votes": " | ".join(votes) if votes else None,
            "action_items": all_actions,
            "key_speakers": sorted(list(all_speakers))
        }

    def generate_protocol_text(self,
                               tops: List[str],
                               top_results: Dict[str, Dict],
                               meeting_metadata: Dict = None) -> str:
        """
        Generate formatted protocol text from TOP results

        Args:
            tops: List of TOP strings
            top_results: Dict mapping TOP string to aggregated results
            meeting_metadata: Optional metadata (date, location, etc.)

        Returns:
            Formatted protocol text
        """
        lines = []

        # Header
        lines.append("=" * 80)
        lines.append("PROTOKOLL DER SITZUNG")
        lines.append("=" * 80)
        lines.append("")

        if meeting_metadata:
            if 'date' in meeting_metadata:
                lines.append(f"Datum: {meeting_metadata['date']}")
            if 'location' in meeting_metadata:
                lines.append(f"Ort: {meeting_metadata['location']}")
            if 'attendees' in meeting_metadata:
                lines.append(f"Anwesend: {meeting_metadata['attendees']}")
            lines.append("")

        lines.append("TAGESORDNUNG UND PROTOKOLL")
        lines.append("")

        # Process each TOP
        for top in tops:
            result = top_results.get(top, {})

            # TOP Header
            lines.append(f"\n{'-' * 80}")
            lines.append(f"{top}")
            lines.append(f"{'-' * 80}\n")

            if not result.get('discussed', False):
                lines.append("⚠ Dieser Tagesordnungspunkt wurde nicht besprochen.\n")
                continue

            # Summary
            lines.append("ZUSAMMENFASSUNG:")
            lines.append(result.get('summary', 'Keine Zusammenfassung verfügbar.'))
            lines.append("")

            # Decisions
            decisions = result.get('decisions', [])
            if decisions:
                lines.append("BESCHLÜSSE:")
                for dec in decisions:
                    lines.append(f"  • {dec}")
                lines.append("")

            # Votes
            votes = result.get('votes')
            if votes:
                lines.append("ABSTIMMUNGEN:")
                lines.append(f"  {votes}")
                lines.append("")

            # Action Items
            actions = result.get('action_items', [])
            if actions:
                lines.append("MAẞNAHMEN:")
                for action in actions:
                    lines.append(f"  • {action}")
                lines.append("")

            # Key Speakers
            speakers = result.get('key_speakers', [])
            if speakers:
                lines.append(f"REDNER: {', '.join(speakers)}")
                lines.append("")

            # Referenced lines
            line_nums = result.get('line_numbers', [])
            if line_nums:
                lines.append(f"REFERENZIERTE ZEILEN: {', '.join(map(str, line_nums[:20]))}" +
                           (" ..." if len(line_nums) > 20 else ""))
                lines.append("")

        # Footer
        lines.append("\n" + "=" * 80)
        lines.append("ENDE DES PROTOKOLLS")
        lines.append("=" * 80)

        return "\n".join(lines)

    def generate_protocol(self,
                         agenda_pdf: str,
                         transcript_file: str,
                         output_file: str,
                         meeting_metadata: Dict = None,
                         topics_file: str = None):
        """
        Complete protocol generation pipeline

        Args:
            agenda_pdf: Path to agenda PDF
            transcript_file: Path to transcript text file
            output_file: Path for output protocol file
            meeting_metadata: Optional metadata about the meeting
            topics_file: Path to intermediate topics file (if None, will be auto-generated)
        """
        print("\n" + "=" * 80)
        print("LLM-BASED MEETING PROTOCOL GENERATION PIPELINE")
        print("=" * 80)

        # Auto-generate topics file path if not provided
        if topics_file is None:
            base_dir = os.path.dirname(agenda_pdf)
            base_name = os.path.splitext(os.path.basename(agenda_pdf))[0]
            topics_file = os.path.join(base_dir, f"{base_name}_topics.txt")

        # Step 1: Extract or load TOPs
        if os.path.exists(topics_file):
            print(f"\n✓ Topics file found: {topics_file}")
            print("Loading topics from file (skip PDF extraction)...")
            tops = self.load_topics_from_file(topics_file)
        else:
            print(f"\n✗ Topics file not found: {topics_file}")
            print("Extracting topics from PDF...")
            tops = self.extract_topics_from_pdf(agenda_pdf)
            if not tops:
                print("ERROR: No TOPs found in agenda PDF!")
                return
            # Save extracted topics
            self.save_topics_to_file(tops, topics_file)

        if not tops:
            print("ERROR: No TOPs available!")
            return

        # Step 2: Load transcript
        # utterances = self.load_transcript(transcript_file)
        # if not utterances:
        #     print("ERROR: No utterances found in transcript!")
        #     return

        # # Step 3: Chunk transcript if needed
        # chunks = self.chunk_transcript(utterances)

        # # Step 4: Process each TOP across all chunks
        # print(f"\n{'-' * 80}")
        # print(f"PROCESSING {len(tops)} TOPs ACROSS {len(chunks)} CHUNKS")
        # print(f"{'-' * 80}\n")

        # top_results = {}
        # for top_idx, top in enumerate(tops, 1):
        #     print(f"\n[{top_idx}/{len(tops)}] Processing: {top}")

        #     chunk_results = []
        #     for chunk_idx, chunk in enumerate(chunks):
        #         result = self.extract_top_discussion(chunk, top, chunk_idx, len(chunks))
        #         chunk_results.append(result)

        #     # Aggregate results for this TOP
        #     aggregated = self.aggregate_top_results(chunk_results, top)
        #     top_results[top] = aggregated

        #     if aggregated['discussed']:
        #         print(f"  ✓ Found discussion: {len(aggregated['line_numbers'])} lines, "
        #               f"{len(aggregated['decisions'])} decisions, "
        #               f"{len(aggregated['key_speakers'])} speakers")
        #     else:
        #         print(f"  ✗ No discussion found")

        # # Step 5: Generate protocol text
        # print(f"\n{'-' * 80}")
        # print("GENERATING PROTOCOL TEXT")
        # print(f"{'-' * 80}\n")

        # protocol_text = self.generate_protocol_text(tops, top_results, meeting_metadata)

        # # Step 6: Save to file
        # with open(output_file, 'w', encoding='utf-8') as f:
        #     f.write(protocol_text)

        # print("\n" + "=" * 80)
        # print("PIPELINE COMPLETE!")
        # print("=" * 80)
        # print(f"\n✓ Protocol saved to: {output_file}")

        # # Summary statistics
        # discussed_count = sum(1 for r in top_results.values() if r['discussed'])
        # print(f"\nSUMMARY:")
        # print(f"  - TOPs processed: {len(tops)}")
        # print(f"  - TOPs with discussion: {discussed_count}")
        # print(f"  - TOPs without discussion: {len(tops) - discussed_count}")


def main():
    """Main entry point"""

    # Initialize protocol generator
    generator = LLMProtocolGenerator(
        ollama_model="gemma3:27b",
        chunk_size=500,
        chunk_overlap=50
    )

    # Configure paths
    agenda_pdf = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250515_LAWI/Einladung_LaWi_15_05_2025-1.pdf"
    transcript_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250910_KA/20250910_Sondersitzung_KA.txt"
    output_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250910_KA/generated_protocol.txt"

    # Meeting metadata (optional)
    meeting_metadata = {
        "date": "10.09.2025",
        "location": "Kreisausschuss, Landkreis Oder-Spree",
        "attendees": "12 von 15 Stimmberechtigten"
    }

    # Run protocol generation pipeline
    generator.generate_protocol(
        agenda_pdf=agenda_pdf,
        transcript_file=transcript_file,
        output_file=output_file,
        meeting_metadata=meeting_metadata
    )


if __name__ == "__main__":
    main()
