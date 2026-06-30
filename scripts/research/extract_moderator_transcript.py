#!/usr/bin/env python3
"""
Extract Moderator Transcript
Extracts only the moderator's utterances from a raw meeting transcript
and calculates size reduction statistics.
"""

import os
import re
import json
from typing import List, Dict


class ModeratorExtractor:
    """Extracts moderator utterances from meeting transcripts"""

    def __init__(self, moderator_speaker_id: str):
        """
        Initialize the extractor

        Args:
            moderator_speaker_id: Speaker ID of the moderator (e.g., "SPEAKER_02")
        """
        self.moderator_speaker_id = moderator_speaker_id
        print(f"Initialized Moderator Extractor")
        print(f"Moderator Speaker ID: {moderator_speaker_id}")

    def load_transcript(self, transcript_path: str) -> List[str]:
        """
        Load raw transcript file

        Args:
            transcript_path: Path to transcript text file

        Returns:
            List of lines from the file
        """
        print(f"\nLoading transcript from {transcript_path}...")

        with open(transcript_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        print(f"✓ Loaded {len(lines)} lines")
        return lines

    def extract_moderator_utterances(self, lines: List[str]) -> List[str]:
        """
        Extract only moderator's utterances (text only, without speaker tags)

        Args:
            lines: List of lines from transcript

        Returns:
            List of moderator's utterance texts (without [SPEAKER_XX]: prefix)
        """
        print(f"\nExtracting moderator utterances...")

        # Pattern: [SPEAKER_XX]: text
        pattern = r'\[SPEAKER_(\d+)\]:\s*(.+)'

        moderator_utterances = []
        total_utterances = 0

        # Extract speaker number from moderator_speaker_id (e.g., "SPEAKER_02" -> "02")
        moderator_num = self.moderator_speaker_id.replace("SPEAKER_", "")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = re.match(pattern, line)
            if match:
                speaker_id = match.group(1)
                text = match.group(2).strip()
                total_utterances += 1

                # Check if this is the moderator
                if speaker_id == moderator_num:
                    moderator_utterances.append(text)

        print(f"✓ Found {len(moderator_utterances)} moderator utterances out of {total_utterances} total utterances")
        print(f"  Moderator percentage: {len(moderator_utterances)/total_utterances*100:.1f}%")

        return moderator_utterances

    def extract_moderator_with_indices(self, lines: List[str]) -> List[Dict]:
        """
        Extract moderator's utterances WITH original transcript indices

        Args:
            lines: List of lines from transcript

        Returns:
            List of dicts: [{"index": int, "text": str}, ...]
        """
        print(f"\nExtracting moderator utterances with indices...")

        # Pattern: [SPEAKER_XX]: text
        pattern = r'\[SPEAKER_(\d+)\]:\s*(.+)'

        moderator_utterances = []
        total_utterances = 0
        current_index = -1  # Track position in combined utterances

        # Extract speaker number from moderator_speaker_id (e.g., "SPEAKER_02" -> "02")
        moderator_num = self.moderator_speaker_id.replace("SPEAKER_", "")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = re.match(pattern, line)
            if match:
                speaker_id = match.group(1)
                text = match.group(2).strip()
                current_index += 1  # Increment for each valid utterance
                total_utterances += 1

                # Check if this is the moderator
                if speaker_id == moderator_num:
                    moderator_utterances.append({
                        "index": current_index,
                        "text": text
                    })

        print(f"✓ Found {len(moderator_utterances)} moderator utterances out of {total_utterances} total utterances")
        print(f"  Moderator percentage: {len(moderator_utterances)/total_utterances*100:.1f}%")
        print(f"  Index range: {moderator_utterances[0]['index']}-{moderator_utterances[-1]['index']}")

        return moderator_utterances

    def save_moderator_transcript(self, utterances: List[str], output_path: str):
        """
        Save moderator utterances to file (one per line)

        Args:
            utterances: List of moderator utterance texts
            output_path: Path to output file
        """
        print(f"\nSaving moderator transcript to {output_path}...")

        with open(output_path, 'w', encoding='utf-8') as f:
            for utterance in utterances:
                f.write(utterance + '\n')

        print(f"✓ Saved {len(utterances)} utterances")

    def save_moderator_with_indices(self, utterances: List[Dict], output_path: str):
        """
        Save moderator utterances with indices to JSON file

        Args:
            utterances: List of dicts with index and text
            output_path: Path to output JSON file
        """
        print(f"\nSaving moderator transcript with indices to {output_path}...")

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(utterances, f, ensure_ascii=False, indent=2)

        print(f"✓ Saved {len(utterances)} utterances with indices")

    def calculate_size_reduction(self, original_path: str, filtered_path: str):
        """
        Calculate and display size reduction statistics

        Args:
            original_path: Path to original transcript
            filtered_path: Path to moderator-only transcript
        """
        print(f"\n{'=' * 80}")
        print("SIZE REDUCTION ANALYSIS")
        print(f"{'=' * 80}\n")

        original_size = os.path.getsize(original_path)
        filtered_size = os.path.getsize(filtered_path)

        reduction_bytes = original_size - filtered_size
        reduction_percentage = (reduction_bytes / original_size) * 100

        print(f"Original transcript size:  {original_size:,} bytes ({original_size / 1024:.2f} KB)")
        print(f"Moderator transcript size: {filtered_size:,} bytes ({filtered_size / 1024:.2f} KB)")
        print(f"\nSize reduction:            {reduction_bytes:,} bytes ({reduction_bytes / 1024:.2f} KB)")
        print(f"Reduction percentage:      {reduction_percentage:.2f}%")
        print(f"\nCompression ratio:         {original_size / filtered_size:.2f}x")

    def extract(self, transcript_path: str, output_path: str = None, json_output_path: str = None):
        """
        Complete extraction pipeline

        Args:
            transcript_path: Path to raw transcript file
            output_path: Path for output text file (auto-generated if not provided)
            json_output_path: Path for output JSON file with indices (auto-generated if not provided)
        """
        print("\n" + "=" * 80)
        print("MODERATOR TRANSCRIPT EXTRACTION PIPELINE")
        print("=" * 80)

        # Auto-generate output paths if not provided
        if output_path is None:
            base_dir = os.path.dirname(transcript_path)
            base_name = os.path.splitext(os.path.basename(transcript_path))[0]
            output_path = os.path.join(base_dir, f"{base_name}_moderator.txt")

        if json_output_path is None:
            base_dir = os.path.dirname(transcript_path)
            base_name = os.path.splitext(os.path.basename(transcript_path))[0]
            json_output_path = os.path.join(base_dir, f"{base_name}_moderator_indexed.json")

        # Step 1: Load transcript
        lines = self.load_transcript(transcript_path)

        # Step 2: Extract moderator utterances (text only)
        moderator_utterances = self.extract_moderator_utterances(lines)

        if not moderator_utterances:
            print("\n⚠️  WARNING: No moderator utterances found!")
            print(f"   Please verify that '{self.moderator_speaker_id}' is correct.")
            return

        # Step 3: Extract moderator utterances (with indices)
        moderator_with_indices = self.extract_moderator_with_indices(lines)

        # Step 4: Save both versions
        self.save_moderator_transcript(moderator_utterances, output_path)
        self.save_moderator_with_indices(moderator_with_indices, json_output_path)

        # Step 5: Calculate size reduction
        self.calculate_size_reduction(transcript_path, output_path)

        print("\n" + "=" * 80)
        print("EXTRACTION COMPLETE!")
        print("=" * 80)
        print(f"\n✓ Moderator transcript (text) saved to: {output_path}")
        print(f"✓ Moderator transcript (with indices) saved to: {json_output_path}")


def main():
    """Main entry point"""

    # Configuration
    moderator_speaker_id = "SPEAKER_18"  # Change this to the actual moderator ID
    transcript_file = "/home/parasmehta/Projects/pilotproject-meeting-summarization-tuiv-ag/data/LK_OS/20250515_LAWI/20250515_LAWI.txt"
    output_file = None  # Auto-generated: will be *_moderator.txt

    # Initialize extractor
    extractor = ModeratorExtractor(moderator_speaker_id=moderator_speaker_id)

    # Run extraction
    extractor.extract(
        transcript_path=transcript_file,
        output_path=output_file
    )


if __name__ == "__main__":
    main()
