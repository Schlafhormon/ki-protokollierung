#!/usr/bin/env python3
"""
Single-Pass LLM Meeting Protocol Generator
Processes entire transcript in one LLM call with all TOPs to avoid duplication
"""

import re
import json
import os
from typing import List, Dict

import requests


class SinglePassProtocolGenerator:
    """Generates meeting protocols using single-pass LLM processing"""

    def __init__(self,
                 ollama_model: str = "gemma3:27b",
                 ollama_url: str = "http://localhost:11434"):
        """
        Initialize the protocol generator

        Args:
            ollama_model: Ollama model name
            ollama_url: Ollama API URL (default: http://localhost:11434)
        """
        self.ollama_model = ollama_model
        self.ollama_url = ollama_url
        print(f"Initialized Single-Pass Protocol Generator with model: {ollama_model}")

    def load_topics_from_file(self, topics_file: str) -> List[str]:
        """Load topics from a text file"""
        print(f"\nLoading topics from {topics_file}...")
        with open(topics_file, 'r', encoding='utf-8') as f:
            topics = [line.strip() for line in f if line.strip()]

        print(f"✓ Loaded {len(topics)} topics from file:")
        for i, topic in enumerate(topics, 1):
            print(f"  {i}. {topic[:80]}..." if len(topic) > 80 else f"  {i}. {topic}")

        return topics

    def load_transcript(self, transcript_path: str) -> str:
        """
        Load and format transcript file

        Args:
            transcript_path: Path to transcript text file

        Returns:
            Formatted transcript string with line numbers
        """
        print(f"\nLoading transcript from {transcript_path}...")

        with open(transcript_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Parse speaker lines: [SPEAKER_XX]: text
        pattern = r'\[SPEAKER_(\d+)\]:\s*(.+)'

        formatted_lines = []
        utterance_count = 0
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue

            match = re.match(pattern, line)
            if match:
                speaker_id = match.group(1)
                text = match.group(2).strip()
                if text:
                    formatted_lines.append(f"[Zeile {line_num}] SPEAKER_{speaker_id}: {text}")
                    utterance_count += 1

        transcript_text = "\n".join(formatted_lines)
        print(f"Loaded {utterance_count} utterances, {len(transcript_text)} characters")
        return transcript_text

    def generate_protocol_with_llm(self, tops: List[str], transcript: str) -> Dict[str, Dict]:
        """
        Generate protocol for all TOPs in a single LLM call

        Args:
            tops: List of TOP strings
            transcript: Full formatted transcript

        Returns:
            Dict mapping each TOP to its protocol data
        """
        print(f"\n{'=' * 80}")
        print("GENERATING PROTOCOL WITH SINGLE LLM CALL")
        print(f"{'=' * 80}\n")

        # Format TOPs as numbered list
        tops_list = "\n".join([f"{i}. {top}" for i, top in enumerate(tops, 1)])

        prompt = f"""You are analyzing a German municipal meeting transcript to generate a protocol.

AGENDA ITEMS (Tagesordnungspunkte):
{tops_list}

FULL TRANSCRIPT:
{transcript}

TASK:
For EACH agenda item above, analyze the transcript and extract:
1. Was this topic discussed? (true/false)
2. Summary of discussion (2-3 sentences in German)
3. Decisions made (list)
4. Votes taken (description or null)
5. Action items (list)
6. Key speakers (list of SPEAKER_XX)
7. Referenced line numbers from transcript

IMPORTANT RULES:
- Process ALL agenda items
- Match each part of transcript to ONLY ONE agenda item (avoid duplication)
- If an agenda item was not discussed at all, set "discussed": false
- Look for direct mentions of topic keywords in the transcript
- Only mark as discussed if there is clear evidence in the transcript

EXAMPLE OUTPUT FORMAT:
{{
  "1. Feststellung der ordnungsgemäßen Einladung und Beschlussfähigkeit": {{
    "discussed": true,
    "summary": "Die Beschlussfähigkeit wurde festgestellt. 12 von 15 Stimmberechtigten waren anwesend.",
    "decisions": [],
    "votes": null,
    "action_items": [],
    "key_speakers": ["SPEAKER_02"],
    "line_numbers": [9, 10, 11]
  }},
  "2. Bestätigung der Tagesordnung": {{
    "discussed": true,
    "summary": "Es gab eine lange Diskussion über Vergabeunterlagen. Die Tagesordnung wurde mit einer Gegenstimme angenommen.",
    "decisions": ["Tagesordnung mit Ergänzung um Punkt 4 'Sonstiges' beschlossen"],
    "votes": "Eine Gegenstimme, Rest Zustimmung",
    "action_items": ["Rechtliche Klärung zur Vergabeunterlagen-Verteilung"],
    "key_speakers": ["SPEAKER_02", "SPEAKER_08", "SPEAKER_01"],
    "line_numbers": [14, 15, 16, 159, 160, 175]
  }}
}}

Return ONLY valid JSON with one entry per agenda item:"""

        print("Calling LLM (this may take several minutes for long transcripts)...")

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_ctx": 131072,  # Use full 128K context
                    }
                },
                timeout=43200  # 12 hours for overnight processing
            )
            response.raise_for_status()

            result = response.json()
            response_text = result.get('response', '').strip()

            print(f"✓ Received response ({len(response_text)} chars)")

            # Try to extract JSON object from response
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                protocol_data = json.loads(json_str)
            else:
                # Fallback: try parsing whole response
                protocol_data = json.loads(response_text)

            print(f"✓ Parsed protocol data for {len(protocol_data)} TOPs")

            return protocol_data

        except requests.exceptions.RequestException as e:
            raise Exception(f"Error calling Ollama API: {e}")
        except json.JSONDecodeError as e:
            print(f"ERROR: Failed to parse JSON response")
            print(f"Response text (first 1000 chars): {response_text[:1000]}")
            raise Exception(f"Error parsing LLM response as JSON: {e}")
        except Exception as e:
            raise Exception(f"Unexpected error: {e}")

    def generate_protocol_text(self,
                               tops: List[str],
                               protocol_data: Dict[str, Dict],
                               meeting_metadata: Dict = None) -> str:
        """
        Generate formatted protocol text

        Args:
            tops: List of TOP strings (in order)
            protocol_data: Dict mapping TOP to its data
            meeting_metadata: Optional metadata

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

        # Process each TOP in order
        for top in tops:
            result = protocol_data.get(top, {})

            # TOP Header
            lines.append(f"\n{'-' * 80}")
            lines.append(f"{top}")
            lines.append(f"{'-' * 80}\n")

            if not result.get('discussed', False):
                lines.append("⚠ Dieser Tagesordnungspunkt wurde nicht besprochen.\n")
                continue

            # Summary
            summary = result.get('summary')
            if summary:
                lines.append("ZUSAMMENFASSUNG:")
                lines.append(summary)
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
                         topics_file: str,
                         transcript_file: str,
                         output_file: str,
                         meeting_metadata: Dict = None):
        """
        Complete protocol generation pipeline

        Args:
            topics_file: Path to topics text file
            transcript_file: Path to transcript text file
            output_file: Path for output protocol file
            meeting_metadata: Optional metadata about the meeting
        """
        print("\n" + "=" * 80)
        print("SINGLE-PASS LLM PROTOCOL GENERATION PIPELINE")
        print("=" * 80)

        # Step 1: Load TOPs
        tops = self.load_topics_from_file(topics_file)
        if not tops:
            print("ERROR: No TOPs found!")
            return

        # Step 2: Load transcript
        transcript = self.load_transcript(transcript_file)
        if not transcript:
            print("ERROR: Empty transcript!")
            return

        # Step 3: Generate protocol with single LLM call
        protocol_data = self.generate_protocol_with_llm(tops, transcript)

        # Step 4: Generate formatted protocol text
        print(f"\n{'-' * 80}")
        print("FORMATTING PROTOCOL TEXT")
        print(f"{'-' * 80}\n")

        protocol_text = self.generate_protocol_text(tops, protocol_data, meeting_metadata)

        # Step 5: Save to file
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(protocol_text)

        print("\n" + "=" * 80)
        print("PIPELINE COMPLETE!")
        print("=" * 80)
        print(f"\n✓ Protocol saved to: {output_file}")

        # Summary statistics
        discussed_count = sum(1 for v in protocol_data.values() if v.get('discussed', False))
        print(f"\nSUMMARY:")
        print(f"  - TOPs processed: {len(tops)}")
        print(f"  - TOPs with discussion: {discussed_count}")
        print(f"  - TOPs without discussion: {len(tops) - discussed_count}")


def main():
    """Main entry point"""

    # Initialize protocol generator
    generator = SinglePassProtocolGenerator(
        ollama_model="gemma3:27b"
    )

    # Configure paths
    topics_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250515_LAWI/Einladung_LaWi_15_05_2025-1_topics.txt"
    transcript_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250515_LAWI/20250515_LAWI.txt"
    output_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250515_LAWI/generated_protocol_single_pass.txt"

    # Meeting metadata (optional)
    meeting_metadata = {
        "date": "15.05.2025",
        "location": "Land- und Forstwirtschaftsausschuss, Landkreis Oder-Spree",
        "attendees": "Information from transcript"
    }

    # Run protocol generation pipeline
    generator.generate_protocol(
        topics_file=topics_file,
        transcript_file=transcript_file,
        output_file=output_file,
        meeting_metadata=meeting_metadata
    )


if __name__ == "__main__":
    main()
