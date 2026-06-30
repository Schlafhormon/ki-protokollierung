#!/usr/bin/env python3
"""
Sequential LLM Meeting Protocol Generator
Exploits the fact that TOPs are discussed in order and none are skipped
"""

import os
import re
import json
from typing import List, Dict

import requests


class SequentialProtocolGenerator:
    """Generates meeting protocols using sequential segmentation"""

    def __init__(self,
                 ollama_model: str = "qwen3:14b",
                 ollama_url: str = "http://localhost:11434",
                 max_context_tokens: int = 40960):
        """
        Initialize the protocol generator

        Args:
            ollama_model: Ollama model name
            ollama_url: Ollama API URL
            max_context_tokens: Maximum context length of the model (default: 40960 for qwen3:14b)
        """
        self.ollama_model = ollama_model
        self.ollama_url = ollama_url
        self.max_context_tokens = max_context_tokens
        print(f"Initialized Sequential Protocol Generator")
        print(f"Model: {ollama_model}")
        print(f"URL: {ollama_url}")
        print(f"Context length: {max_context_tokens} tokens")

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count from text using character-based heuristic

        Args:
            text: Input text string

        Returns:
            Estimated number of tokens (1 token ≈ 4 characters)
        """
        return len(text) // 4

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

        print(f"✓ Loaded {len(topics)} topics:")
        for i, topic in enumerate(topics, 1):
            print(f"  {i}. {topic[:80]}{'...' if len(topic) > 80 else ''}")

        return topics

    def load_transcript(self, transcript_path: str) -> List[Dict[str, any]]:
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

        print(f"✓ Loaded {len(utterances)} utterances from "
              f"{len(set(u['speaker'] for u in utterances))} speakers")
        return utterances

    def combine_consecutive_speakers(self, utterances: List[Dict]) -> List[Dict]:
        """
        Combine consecutive utterances from the same speaker

        Args:
            utterances: List of utterance dicts

        Returns:
            List of combined utterances with only speaker and text
        """
        if not utterances:
            return []

        print(f"\nCombining consecutive same-speaker utterances...")

        combined = []
        current = {
            'speaker': utterances[0]['speaker'],
            'text': utterances[0]['text']
        }

        for utt in utterances[1:]:
            if utt['speaker'] == current['speaker']:
                # Same speaker - combine
                current['text'] += " " + utt['text']
            else:
                # Different speaker - save current and start new
                combined.append(current)
                current = {
                    'speaker': utt['speaker'],
                    'text': utt['text']
                }

        # Don't forget the last one
        combined.append(current)

        print(f"✓ Combined {len(utterances)} utterances → {len(combined)} combined utterances")
        return combined

    def save_combined_utterances(self, utterances: List[Dict], output_path: str):
        """
        Save combined utterances to a JSON file
        This is the output of the transcription pipeline

        Args:
            utterances: List of combined utterance dicts
            output_path: Path to output JSON file
        """
        print(f"\nSaving combined utterances to {output_path}...")

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(utterances, f, ensure_ascii=False, indent=2)

        print(f"✓ Saved {len(utterances)} combined utterances")

    def load_combined_utterances(self, input_path: str) -> List[Dict]:
        """
        Load combined utterances from a JSON file
        This is the input to the protocol generation pipeline

        Args:
            input_path: Path to JSON file with combined utterances

        Returns:
            List of combined utterance dicts
        """
        print(f"\nLoading combined utterances from {input_path}...")

        with open(input_path, 'r', encoding='utf-8') as f:
            utterances = json.load(f)

        print(f"✓ Loaded {len(utterances)} combined utterances from "
              f"{len(set(u['speaker'] for u in utterances))} speakers")
        return utterances

    def find_single_boundary(self,
                           current_top: str,
                           next_top: str,
                           remaining_utterances: List[Dict],
                           absolute_start_idx: int) -> int:
        """
        Find where the current TOP ends using LLM

        Args:
            current_top: Current TOP string
            next_top: Next TOP string
            remaining_utterances: Utterances from current position to end
            absolute_start_idx: Absolute index in full transcript where window starts

        Returns:
            Absolute index where current TOP ends
        """
        # Build base prompt to estimate its token cost
        base_prompt_template = """You are finding where one agenda item ends and the next begins in a German municipal meeting.

CURRENT AGENDA ITEM:
{current_top}

NEXT AGENDA ITEM:
{next_top}

TRANSCRIPT (showing from current TOP to end of meeting):
{{TRANSCRIPT_PLACEHOLDER}}

TASK:
Find the LAST index that belongs to the CURRENT agenda item.

The NEXT agenda item starts at the index after your answer.

HINTS:
- Look for explicit mentions of the next TOP's topic
- Look for transition phrases: "kommen wir zum nächsten Punkt", "ich rufe auf", "Tagesordnungspunkt"
- Look for topic shifts in the discussion
- The current TOP ends just before the next TOP begins

EXAMPLE:
If index 20 completes discussion of current TOP and index 21 starts next TOP, return 20.

Return ONLY valid JSON:
{{
  "boundary_index": <the last index of current TOP>,
  "reasoning": "<brief explanation>"
}}"""

        base_prompt = base_prompt_template.format(
            current_top=current_top,
            next_top=next_top
        )

        # Calculate available token budget
        base_tokens = self.estimate_tokens(base_prompt)
        response_buffer = 500  # Reserve tokens for LLM response
        available_tokens = self.max_context_tokens - base_tokens - response_buffer

        print(f"    Token budget: {available_tokens} tokens available for utterances")

        # Format remaining transcript with utterance indices, respecting token budget
        transcript_text = ""
        included_count = 0
        truncated = False

        for i, utt in enumerate(remaining_utterances):
            absolute_idx = absolute_start_idx + i
            utt_line = f"[Utterance {absolute_idx}] [{utt['speaker']}]: {utt['text']}\n"

            # Check if adding this utterance would exceed budget
            new_total = self.estimate_tokens(transcript_text + utt_line)
            if new_total > available_tokens:
                truncated = True
                print(f"    ⚠️  Context limit reached: including {included_count}/{len(remaining_utterances)} utterances")
                print(f"    Estimated tokens used: ~{self.estimate_tokens(transcript_text)} / {available_tokens}")
                break

            transcript_text += utt_line
            included_count += 1

        if not truncated:
            print(f"    ✓ All {included_count} utterances fit in context (~{self.estimate_tokens(transcript_text)} tokens)")

        prompt = f"""You are finding where one agenda item ends and the next begins in a German municipal meeting.

CURRENT AGENDA ITEM:
{current_top}

NEXT AGENDA ITEM:
{next_top}

TRANSCRIPT (showing from current TOP to end of meeting):
{transcript_text}

TASK:
Find the LAST index that belongs to the CURRENT agenda item.

The NEXT agenda item starts at the index after your answer.

HINTS:
- Look for explicit mentions of the next TOP's topic
- Look for transition phrases: "kommen wir zum nächsten Punkt", "ich rufe auf", "Tagesordnungspunkt"
- Look for topic shifts in the discussion
- The current TOP ends just before the next TOP begins

EXAMPLE:
If index 20 completes discussion of current TOP and index 21 starts next TOP, return 20.

Return ONLY valid JSON:
{{
  "boundary_index": <the last index of current TOP>,
  "reasoning": "<brief explanation>"
}}"""

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                    }
                },
                timeout=600
            )
            response.raise_for_status()

            result = response.json()
            response_text = result.get('response', '').strip()

            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                boundary_result = json.loads(json_str)
            else:
                boundary_result = json.loads(response_text)

            boundary_idx = boundary_result.get('boundary_index')
            reasoning = boundary_result.get('reasoning', 'No reasoning provided')

            print(f"    LLM reasoning: {reasoning[:100]}{'...' if len(reasoning) > 100 else ''}")

            # Validate boundary is within range
            max_idx = absolute_start_idx + len(remaining_utterances) - 1
            if boundary_idx < absolute_start_idx or boundary_idx > max_idx:
                print(f"\n    ⚠️  CRITICAL WARNING: LLM QUALITY ISSUE DETECTED ⚠️")
                print(f"    Boundary {boundary_idx} is OUT OF RANGE [{absolute_start_idx}, {max_idx}]")
                print(f"    This suggests poor LLM reasoning or hallucination!")
                print(f"    Clipping to valid range, but results may be unreliable.\n")
                boundary_idx = min(max_idx, max(absolute_start_idx, boundary_idx))

            return boundary_idx

        except Exception as e:
            print(f"    ERROR: Failed to find boundary: {e}")
            # Fallback: split remaining utterances in half
            fallback = absolute_start_idx + len(remaining_utterances) // 2
            print(f"    Using fallback boundary: {fallback}")
            return fallback

    def find_top_boundaries(self, tops: List[str], utterances: List[Dict]) -> Dict[str, Dict]:
        """
        Find where each TOP begins and ends in the transcript using sliding window

        Args:
            tops: List of TOP strings (in order)
            utterances: List of combined utterance dicts

        Returns:
            Dict mapping TOP to {"start_idx": int, "end_idx": int}
        """
        print(f"\n{'=' * 80}")
        print("FINDING TOP BOUNDARIES IN TRANSCRIPT (Sliding Window)")
        print(f"{'=' * 80}\n")
        print(f"Processing {len(tops)} TOPs with {len(utterances)} utterances")
        print(f"This will require {len(tops) - 1} boundary detection calls\n")

        boundaries = {}
        current_start = 0

        # Find boundaries one at a time
        for i in range(len(tops) - 1):
            current_top = tops[i]
            next_top = tops[i + 1]

            print(f"[{i+1}/{len(tops)-1}] Finding boundary between:")
            print(f"  Current: {current_top[:70]}...")
            print(f"  Next: {next_top[:70]}...")

            # Get remaining transcript from current position
            remaining = utterances[current_start:]
            print(f"  Analyzing {len(remaining)} remaining utterances (indices {current_start}-{len(utterances)-1})")

            # Find where current TOP ends
            boundary_idx = self.find_single_boundary(
                current_top=current_top,
                next_top=next_top,
                remaining_utterances=remaining,
                absolute_start_idx=current_start
            )

            # Store boundary for current TOP
            boundaries[current_top] = {
                "start_idx": current_start,
                "end_idx": boundary_idx
            }

            print(f"  ✓ Boundary found: TOP ends at index {boundary_idx}\n")

            # Next TOP starts where this one ends
            current_start = boundary_idx + 1

        # Last TOP gets everything remaining
        last_top = tops[-1]
        boundaries[last_top] = {
            "start_idx": current_start,
            "end_idx": len(utterances) - 1
        }

        print(f"[Final] Last TOP '{last_top[:70]}...' gets remaining utterances")
        print(f"  Indices: {current_start}-{len(utterances)-1}\n")

        # Summary
        print(f"{'=' * 80}")
        print("BOUNDARY DETECTION COMPLETE")
        print(f"{'=' * 80}\n")
        print("Summary:")
        for top, bounds in boundaries.items():
            span = bounds['end_idx'] - bounds['start_idx'] + 1
            print(f"  {top[:60]}...")
            print(f"    → indices {bounds['start_idx']}-{bounds['end_idx']} ({span} utterances)")

        return boundaries

    def save_boundaries(self, boundaries: Dict[str, Dict], output_path: str):
        """
        Save TOP boundaries to a JSON file
        This is the output of the boundary detection phase

        Args:
            boundaries: Dict mapping TOP to {"start_idx": int, "end_idx": int}
            output_path: Path to output JSON file
        """
        print(f"\nSaving boundaries to {output_path}...")

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(boundaries, f, ensure_ascii=False, indent=2)

        print(f"✓ Saved boundaries for {len(boundaries)} TOPs")

    def load_boundaries(self, input_path: str) -> Dict[str, Dict]:
        """
        Load TOP boundaries from a JSON file
        This allows skipping the boundary detection phase

        Args:
            input_path: Path to JSON file with boundaries

        Returns:
            Dict mapping TOP to {"start_idx": int, "end_idx": int}
        """
        print(f"\nLoading boundaries from {input_path}...")

        with open(input_path, 'r', encoding='utf-8') as f:
            boundaries = json.load(f)

        print(f"✓ Loaded boundaries for {len(boundaries)} TOPs")

        # Print summary
        print("\nBoundary summary:")
        for top, bounds in boundaries.items():
            span = bounds['end_idx'] - bounds['start_idx'] + 1
            print(f"  {top[:60]}...")
            print(f"    → indices {bounds['start_idx']}-{bounds['end_idx']} ({span} utterances)")

        return boundaries

    def process_top_segment(self, top: str, segment: List[Dict], start_idx: int, end_idx: int) -> Dict:
        """
        Process a single TOP segment to extract protocol information

        Args:
            top: TOP string
            segment: List of utterances for this TOP
            start_idx: Starting utterance index
            end_idx: Ending utterance index

        Returns:
            Dict with summary, decisions, votes, action_items, key_speakers, utterance_indices
        """
        print(f"\n  Processing: {top[:60]}...")

        # Check for empty segment
        if not segment:
            print(f"    ✗ Empty segment - no utterances found")
            return {
                "summary": f"Keine Diskussion zu {top} gefunden.",
                "decisions": [],
                "votes": None,
                "action_items": [],
                "key_speakers": [],
                "utterance_indices": []
            }

        # Format segment (just index within segment, speaker, and text)
        segment_text = ""
        for i, utt in enumerate(segment):
            utterance_idx = start_idx + i
            segment_text += f"[Utterance {utterance_idx}] {utt['speaker']}: {utt['text']}\n"

        prompt = f"""You are analyzing a German municipal meeting transcript for a specific agenda item.

AGENDA ITEM:
{top}

TRANSCRIPT SECTION FOR THIS AGENDA ITEM:
{segment_text}

TASK:
Extract the following information as JSON:
{{
  "summary": "2-3 sentence summary in German describing what was discussed",
  "decisions": ["List of decisions made (Beschlüsse)"],
  "votes": "Description of any votes taken, or null if no votes",
  "action_items": ["List of action items (Maßnahmen) with responsible parties"],
  "key_speakers": ["SPEAKER_02", "SPEAKER_08"]
}}

IMPORTANT:
- Write summary in German
- Be concise but capture key points
- Include ALL speakers who contributed substantially
- If no decisions/votes/actions, use empty list or null

Return ONLY valid JSON:"""

        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        # Use low temperature for consistent, factual extraction
                        "temperature": 0.2,
                    }
                },
                timeout=600
            )
            response.raise_for_status()

            result = response.json()
            response_text = result.get('response', '').strip()

            # Extract JSON
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                extracted = json.loads(json_str)
            else:
                extracted = json.loads(response_text)

            # Add utterance index range
            extracted['utterance_indices'] = [start_idx, end_idx]

            print(f"    ✓ Extracted: {len(extracted.get('decisions', []))} decisions, "
                  f"{len(extracted.get('action_items', []))} actions, "
                  f"{len(extracted.get('key_speakers', []))} speakers")

            return extracted

        except Exception as e:
            print(f"    ✗ Error processing segment: {e}")
            return {
                "summary": f"Fehler bei der Verarbeitung von {top}",
                "decisions": [],
                "votes": None,
                "action_items": [],
                "key_speakers": [],
                "utterance_indices": [start_idx, end_idx] if segment else []
            }

    def generate_protocol_text(self,
                               tops: List[str],
                               top_results: Dict[str, Dict],
                               meeting_metadata: Dict = None) -> str:
        """
        Generate formatted protocol text

        Args:
            tops: List of TOP strings (in order)
            top_results: Dict mapping TOP to its protocol data
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

        # Process each TOP
        for top in tops:
            result = top_results.get(top, {})

            # TOP Header
            lines.append(f"\n{'-' * 80}")
            lines.append(f"{top}")
            lines.append(f"{'-' * 80}\n")

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

            # Referenced utterances
            utterance_indices = result.get('utterance_indices', [])
            if utterance_indices and len(utterance_indices) >= 2:
                lines.append(f"REFERENZIERTE UTTERANCES: {utterance_indices[0]}-{utterance_indices[1]}")
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
                         meeting_metadata: Dict = None,
                         combined_utterances_file: str = None,
                         boundaries_file: str = None):
        """
        Complete protocol generation pipeline

        Args:
            topics_file: Path to topics text file
            transcript_file: Path to transcript text file
            output_file: Path for output protocol file
            meeting_metadata: Optional metadata about the meeting
            combined_utterances_file: Optional path to combined utterances JSON file.
                                     If not provided, will be auto-generated from transcript_file path.
            boundaries_file: Optional path to boundaries JSON file.
                           If not provided, will be auto-generated from transcript_file path.
        """
        print("\n" + "=" * 80)
        print("SEQUENTIAL PROTOCOL GENERATION PIPELINE")
        print("=" * 80)

        # Step 1: Load TOPs
        tops = self.load_topics_from_file(topics_file)
        if not tops:
            print("ERROR: No TOPs found!")
            return

        # Step 2: Load or generate combined utterances
        # Auto-generate combined utterances file path if not provided
        if combined_utterances_file is None:
            base_dir = os.path.dirname(transcript_file)
            base_name = os.path.splitext(os.path.basename(transcript_file))[0]
            combined_utterances_file = os.path.join(base_dir, f"{base_name}_combined.json")

        # Check if combined utterances already exist
        if os.path.exists(combined_utterances_file):
            print(f"\n✓ Found existing combined utterances file: {combined_utterances_file}")
            print("Loading from file (skipping transcription processing)...")
            combined = self.load_combined_utterances(combined_utterances_file)
        else:
            print(f"\n✗ Combined utterances file not found: {combined_utterances_file}")
            print("Processing raw transcript (transcription pipeline)...")

            # Load raw transcript
            utterances = self.load_transcript(transcript_file)
            if not utterances:
                print("ERROR: Empty transcript!")
                return

            # Combine consecutive same-speaker utterances
            combined = self.combine_consecutive_speakers(utterances)

            # Save combined utterances (end of transcription pipeline)
            self.save_combined_utterances(combined, combined_utterances_file)

        # Step 3: Find TOP boundaries (protocol generation pipeline starts here)
        # Auto-generate boundaries file path if not provided
        if boundaries_file is None:
            base_dir = os.path.dirname(transcript_file)
            base_name = os.path.splitext(os.path.basename(transcript_file))[0]
            boundaries_file = os.path.join(base_dir, f"{base_name}_boundaries.json")

        # Check if boundaries already exist
        if os.path.exists(boundaries_file):
            print(f"\n✓ Found existing boundaries file: {boundaries_file}")
            print("Loading from file (skipping boundary detection)...")
            boundaries = self.load_boundaries(boundaries_file)
        else:
            print(f"\n✗ Boundaries file not found: {boundaries_file}")
            print("Running boundary detection (Phase A)...")

            # Run boundary detection
            boundaries = self.find_top_boundaries(tops, combined)

            # Save boundaries (end of Phase A)
            self.save_boundaries(boundaries, boundaries_file)

        # # Step 4: Process each TOP segment
        # print(f"\n{'-' * 80}")
        # print(f"PROCESSING {len(tops)} TOP SEGMENTS")
        # print(f"{'-' * 80}")

        # top_results = {}
        # for top in tops:
        #     if top not in boundaries:
        #         print(f"\n  WARNING: No boundary found for {top[:60]}...")
        #         continue

        #     bounds = boundaries[top]
        #     start_idx = bounds['start_idx']
        #     end_idx = bounds['end_idx']

        #     # Extract segment
        #     segment = combined[start_idx:end_idx + 1]

        #     # Process segment
        #     result = self.process_top_segment(top, segment, start_idx, end_idx)
        #     top_results[top] = result

        # # Step 5: Generate protocol text
        # print(f"\n{'-' * 80}")
        # print("FORMATTING PROTOCOL TEXT")
        # print(f"{'-' * 80}\n")

        # protocol_text = self.generate_protocol_text(tops, top_results, meeting_metadata)

        # # Step 6: Save to file
        # with open(output_file, 'w', encoding='utf-8') as f:
        #     f.write(protocol_text)

        # print("\n" + "=" * 80)
        # print("PIPELINE COMPLETE!")
        # print("=" * 80)
        # print(f"\n✓ Protocol saved to: {output_file}")

        # Summary
        print(f"\nSUMMARY:")
        print(f"  - TOPs processed: {len(tops)}")
        print(f"  - Combined utterances: {len(combined)}")
        print(f"  - Combined utterances file: {combined_utterances_file}")
        print(f"  - Boundaries file: {boundaries_file}")


def main():
    """Main entry point"""

    # Initialize generator
    generator = SequentialProtocolGenerator(
        ollama_model="qwen3:14b"
    )

    # Configure paths
    topics_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250515_LAWI/Einladung_LaWi_15_05_2025-1_topics.txt"
    transcript_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250515_LAWI/20250515_LAWI.txt"
    output_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250515_LAWI/generated_protocol_sequential_1.txt"

    # Meeting metadata
    meeting_metadata = {
        "date": "15.05.2025",
        "location": "Land- und Forstwirtschaftsausschuss, Landkreis Oder-Spree",
        "attendees": "Information from transcript"
    }

    # Run pipeline
    generator.generate_protocol(
        topics_file=topics_file,
        transcript_file=transcript_file,
        output_file=output_file,
        meeting_metadata=meeting_metadata
    )


if __name__ == "__main__":
    main()
