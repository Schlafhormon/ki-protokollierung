#!/usr/bin/env python3
"""
Single-Pass Moderator-Based Segmentation
Uses moderator transcript to segment meeting by TOPs in one LLM call
"""

import os
import re
import json
from typing import List, Dict

import requests


class ModeratorSegmenter:
    """Segments meeting transcript using moderator utterances"""

    def __init__(self,
                 model: str = "nvidia/Llama-3.3-70B-Instruct-FP8",
                 base_url: str = "https://chat.hpi-sci.de",
                 api_key: str = None):
        """
        Initialize the segmenter

        Args:
            model: Model name (e.g., "nvidia/Llama-3.3-70B-Instruct-FP8")
            base_url: OpenWebUI base URL
            api_key: API key for authentication (can also use environment variable OPENWEBUI_API_KEY)
        """
        self.model = model
        self.base_url = base_url.rstrip('/')
        self.api_url = f"{self.base_url}/api/chat/completions"

        # Get API key from parameter or environment variable
        self.api_key = api_key or os.environ.get('OPENWEBUI_API_KEY')
        if not self.api_key:
            raise ValueError(
                "API key required! Provide via api_key parameter or OPENWEBUI_API_KEY environment variable"
            )

        print(f"Initialized Moderator Segmenter")
        print(f"Model: {model}")
        print(f"Base URL: {base_url}")
        print(f"API Endpoint: {self.api_url}")

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

    def load_moderator_indexed(self, moderator_file: str) -> List[Dict]:
        """
        Load moderator utterances with indices from JSON file

        Args:
            moderator_file: Path to JSON file with moderator utterances

        Returns:
            List of dicts: [{"index": int, "text": str}, ...]
        """
        print(f"\nLoading moderator transcript from {moderator_file}...")

        with open(moderator_file, 'r', encoding='utf-8') as f:
            moderator_data = json.load(f)

        print(f"✓ Loaded {len(moderator_data)} moderator utterances")
        print(f"  Index range: {moderator_data[0]['index']}-{moderator_data[-1]['index']}")

        return moderator_data

    def format_tops_list(self, tops: List[str]) -> str:
        """
        Format TOPs list for prompt

        Args:
            tops: List of TOP strings

        Returns:
            Formatted string
        """
        lines = []
        for i, top in enumerate(tops, 1):
            lines.append(f"{i}. {top}")
        return "\n".join(lines)

    def format_moderator_utterances(self, moderator_data: List[Dict]) -> str:
        """
        Format moderator utterances for prompt

        Args:
            moderator_data: List of dicts with index and text

        Returns:
            Formatted string
        """
        lines = []
        for item in moderator_data:
            lines.append(f"[Index {item['index']}] {item['text']}")
        return "\n".join(lines)

    def segment_by_moderator(self, tops: List[str], moderator_data: List[Dict]) -> Dict[str, Dict]:
        """
        Segment meeting by TOPs using single LLM call on moderator transcript

        Args:
            tops: List of TOP strings in order
            moderator_data: List of moderator utterances with indices

        Returns:
            Dict mapping TOP to {"start_index": int, "end_index": int,
                                 "announcement_index": int/null, "transition_type": str}
        """
        print(f"\n{'=' * 80}")
        print("SINGLE-PASS MODERATOR SEGMENTATION")
        print(f"{'=' * 80}\n")
        print(f"Segmenting {len(tops)} TOPs using {len(moderator_data)} moderator utterances")
        print("This requires only 1 LLM call!\n")

        # Build prompt
        prompt = f"""You are analyzing a German municipal meeting transcript to segment it by agenda items (Tagesordnungspunkte/TOPs).

You will receive:
1. A list of TOPs in the order they appear in the agenda
2. Moderator utterances with their original transcript indices

The moderator announces transitions between TOPs. Your task is to find where each TOP begins and ends.

================================================================================
LIST OF TOPs (IN ORDER):
================================================================================
{self.format_tops_list(tops)}

================================================================================
MODERATOR UTTERANCES (WITH ORIGINAL TRANSCRIPT INDICES):
================================================================================
{self.format_moderator_utterances(moderator_data)}

================================================================================
TASK:
================================================================================
For each TOP in the list, identify:
1. START INDEX: The first utterance index where this TOP begins
2. END INDEX: The last utterance index before the next TOP starts
3. ANNOUNCEMENT INDEX: Where the moderator explicitly announces this TOP (if found)

IMPORTANT RULES:
1. TOPs are discussed IN ORDER by the moderator
2. The first TOP starts at index 0 (meeting opening)
3. Each TOP ends immediately before the next TOP begins (no gaps, no overlaps)
4. The announcement_index should be where the moderator INTRODUCES the TOP
5. If no clear announcement is found for a TOP, set announcement_index to null
6. The last TOP ends at the last moderator utterance index

HOW TO IDENTIFY TRANSITIONS:

EXPLICIT transitions - moderator clearly announces the TOP change:
  - Direct announcements: "Tagesordnungspunkt", "ich rufe auf", "kommen wir zu"
  - Numbered references: "Punkt 3", "TOP 2"
  - Sequential phrases: "nächsten Punkt", "als nächstes", "dann haben wir"
  - Procedural language: any clear statement that a new agenda item is starting

IMPLICIT transitions - moderator begins discussing the new topic:
  - Starts talking about the new TOP's subject matter without formal announcement
  - References the topic from the TOP description (even partially)
  - Topic shift detectable from comparing TOP description to moderator's words
  - Example: If TOP is "Haushalt 2025", moderator might just say "Zum Haushalt..." without "Tagesordnungspunkt"

MATCHING STRATEGY:
1. Compare each TOP's description/subject to the moderator's utterances
2. Look for keywords, themes, or subject matter from the TOP appearing in moderator speech
3. Consider the sequential order - TOPs are discussed in order, none are skipped
4. Trust topic continuity - when moderator shifts from one subject to another, that's likely a boundary

EXAMPLES:

Example 1 - Explicit transition:
Moderator at index 42: "Kommen wir zum nächsten Tagesordnungspunkt - Haushalt 2025"
TOP: "TOP 3: Haushalt 2025"
→ start_index: 42, announcement_index: 42, transition_type: "explicit"

Example 2 - Implicit transition:
Moderator at index 67: "Zum Thema Schulneubau möchte ich sagen..."
TOP: "TOP 5: Neubau der Grundschule"
→ start_index: 67, announcement_index: 67, transition_type: "implicit"

Example 3 - Inferred transition:
TOP 4 clearly ends at index 89 (next TOP announced at 90)
TOP 5 clearly starts at index 120
Moderator utterances 90-119 don't clearly announce anything
→ But by sequential order, this must be TOP 5's discussion
→ start_index: 90, announcement_index: null, transition_type: "implicit"

================================================================================
OUTPUT FORMAT (VALID JSON ONLY):
================================================================================
{{
  "segments": [
    {{
      "top": "<exact TOP string from the list>",
      "start_index": <first index of this TOP>,
      "end_index": <last index of this TOP>,
      "announcement_index": <index where transition is detected, or null if unclear>,
      "transition_type": "explicit" or "implicit",
      "reasoning": "<brief explanation: what moderator said and how it matches the TOP>"
    }},
    ...
  ]
}}

REASONING FIELD:
In the reasoning, quote relevant moderator words and explain the match:
- Good: "At index 42, moderator says 'Haushalt 2025' matching TOP title"
- Good: "At index 67, moderator discusses school building, matching 'Neubau der Grundschule'"
- Good: "No clear announcement, but by sequential order this range must be TOP 4"

Return ONLY valid JSON. Do not include any other text before or after the JSON.
"""

        try:
            print("Sending request to LLM...")

            # Prepare OpenAI-compatible request
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.1,
                "stream": False
            }

            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=600
            )

            # Debug: Print error details if request failed
            if response.status_code != 200:
                print(f"\n✗ API Error (Status {response.status_code}):")
                print(f"Response body: {response.text[:500]}")
                print(f"\nRequest payload preview:")
                print(f"  Model: {payload['model']}")
                print(f"  Message length: {len(payload['messages'][0]['content'])} chars")

            response.raise_for_status()

            result = response.json()

            # Extract content from OpenAI-style response
            response_text = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()

            if not response_text:
                raise ValueError("Empty response from API")

            print("✓ Received response from LLM\n")

            # DEBUG: Save raw response to see what went wrong
            with open('/tmp/llm_response_debug.txt', 'w', encoding='utf-8') as f:
                f.write(response_text)
            print(f"Debug: Saved raw response to /tmp/llm_response_debug.txt\n")
            
            # Extract JSON from response
            try:
                segmentation = self.extract_json_from_response(response_text)
            except ValueError as e:
                print(f"\n✗ Failed to extract JSON from response")
                print(f"Response preview (first 500 chars):\n{response_text[:500]}\n")
                raise

            segments = segmentation.get('segments', [])

            # Validate we got all TOPs
            if len(segments) != len(tops):
                print(f"\n⚠️  WARNING: Expected {len(tops)} segments, got {len(segments)}")
                print(f"   This may be due to duplicate/overlapping TOPs in the topics file.")
                print(f"   Missing segments will have None values.")

            # Convert to dict mapping TOP to boundary info
            boundaries = {}
            for seg in segments:
                top = seg.get('top')
                boundaries[top] = {
                    'start_index': seg.get('start_index'),
                    'end_index': seg.get('end_index'),
                    'announcement_index': seg.get('announcement_index'),
                    'transition_type': seg.get('transition_type'),
                    'reasoning': seg.get('reasoning', '')
                }

            # Add missing TOPs with None values
            for expected_top in tops:
                if expected_top not in boundaries:
                    boundaries[expected_top] = {
                        'start_index': None,
                        'end_index': None,
                        'announcement_index': None,
                        'transition_type': 'missing',
                        'reasoning': 'LLM did not return this segment'
                    }

            # Print summary
            print(f"{'=' * 80}")
            print("SEGMENTATION COMPLETE")
            print(f"{'=' * 80}\n")
            print("Summary:")
            for top, info in boundaries.items():
                # Handle missing or incomplete segments
                start_idx = info.get('start_index')
                end_idx = info.get('end_index')
                transition = info.get('transition_type', 'unknown')
                announce_idx = info.get('announcement_index')
                reasoning = info.get('reasoning', 'No reasoning provided')

                print(f"\n  {top[:60]}...")

                if start_idx is not None and end_idx is not None:
                    span = end_idx - start_idx + 1
                    print(f"    → Indices: {start_idx}-{end_idx} ({span} utterances)")
                else:
                    print(f"    → Indices: MISSING (start={start_idx}, end={end_idx})")

                print(f"    → Transition: {transition} (announced at: {announce_idx if announce_idx else 'N/A'})")
                print(f"    → Reasoning: {reasoning[:100]}{'...' if len(reasoning) > 100 else ''}")

            return boundaries

        except Exception as e:
            print(f"\n✗ ERROR: Failed to segment: {e}")
            raise

    def extract_json_from_response(self, response_text: str) -> dict:
        """ Robustly extract JSON from LLM response. Handles markdown, extra text, and malformed output """
        # Strategy 1: Look for JSON in markdown code blocks
        code_block_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        code_match = re.search(code_block_pattern, response_text, re.DOTALL)
        if code_match:
            try:
                return json.loads(code_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Strategy 2: Find JSON with balanced braces (most robust)
        brace_count = 0
        start_idx = None
        
        for i, char in enumerate(response_text):
            if char == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_idx is not None:
                    try:
                        json_str = response_text[start_idx:i+1]
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        # This wasn't valid JSON, keep looking
                        start_idx = None
                        continue
        
        # Strategy 3: Try to find any JSON-like structure
        # Find first { and try to parse from there
        first_brace = response_text.find('{')
        if first_brace != -1:
            # Try progressively longer substrings
            for end in range(len(response_text), first_brace, -1):
                try:
                    return json.loads(response_text[first_brace:end])
                except json.JSONDecodeError:
                    continue
        
        raise ValueError("No valid JSON found in response")

    def save_boundaries(self, boundaries: Dict[str, Dict], output_path: str):
        """
        Save boundaries to JSON file

        Args:
            boundaries: Dict mapping TOP to boundary info
            output_path: Path to output JSON file
        """
        print(f"\nSaving boundaries to {output_path}...")

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(boundaries, f, ensure_ascii=False, indent=2)

        print(f"✓ Saved boundaries for {len(boundaries)} TOPs")

    def segment(self,
                topics_file: str,
                moderator_indexed_file: str,
                output_file: str = None):
        """
        Complete segmentation pipeline

        Args:
            topics_file: Path to topics text file
            moderator_indexed_file: Path to moderator JSON file with indices
            output_file: Path for output boundaries JSON (auto-generated if not provided)
        """
        print("\n" + "=" * 80)
        print("MODERATOR-BASED SEGMENTATION PIPELINE")
        print("=" * 80)

        # Auto-generate output path if not provided
        if output_file is None:
            base_dir = os.path.dirname(moderator_indexed_file)
            base_name = os.path.splitext(os.path.basename(moderator_indexed_file))[0]
            # Remove _moderator_indexed suffix if present
            base_name = base_name.replace('_moderator_indexed', '')
            output_file = os.path.join(base_dir, f"{base_name}_boundaries_moderator.json")

        # Step 1: Load TOPs
        tops = self.load_topics_from_file(topics_file)
        if not tops:
            print("ERROR: No TOPs found!")
            return

        # Step 2: Load moderator transcript
        moderator_data = self.load_moderator_indexed(moderator_indexed_file)
        if not moderator_data:
            print("ERROR: No moderator utterances found!")
            return

        # Step 3: Segment using single LLM call
        boundaries = self.segment_by_moderator(tops, moderator_data)

        # Step 4: Save boundaries
        self.save_boundaries(boundaries, output_file)

        print("\n" + "=" * 80)
        print("PIPELINE COMPLETE!")
        print("=" * 80)
        print(f"\n✓ Boundaries saved to: {output_file}")


def main():
    """Main entry point"""

    # Get API key from environment variable or prompt user
    api_key = os.environ.get('OPENWEBUI_API_KEY')
    if not api_key:
        print("⚠️  OPENWEBUI_API_KEY environment variable not set!")
        print("Set it with: export OPENWEBUI_API_KEY='your-api-key-here'")
        print("Get your API key from: https://chat.hpi-sci.de -> Settings -> Account")
        return

    # Initialize segmenter with OpenWebUI API
    segmenter = ModeratorSegmenter(
        model="nvidia/Llama-3.3-70B-Instruct-FP8",
        base_url="https://chat.hpi-sci.de",
        api_key=api_key
    )

    # Configure paths
    topics_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250515_LAWI/Einladung_LaWi_15_05_2025-1_topics.txt"
    moderator_file = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250515_LAWI/20250515_LAWI_moderator_indexed.json"
    output_file = None  # Auto-generated

    # Run segmentation
    segmenter.segment(
        topics_file=topics_file,
        moderator_indexed_file=moderator_file,
        output_file=output_file
    )


if __name__ == "__main__":
    main()
