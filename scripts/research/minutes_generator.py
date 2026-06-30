#!/usr/bin/env python3
"""
Meeting Minutes Generator
Generates formal German meeting minutes (Niederschrift) from transcript and TOP boundaries
"""

import os
import re
import json
from typing import List, Dict, Tuple
from datetime import datetime

import requests


class MinutesGenerator:
    """Generates formal meeting minutes from segmented transcript"""

    def __init__(self,
                 model: str = "nvidia/Llama-3.3-70B-Instruct-FP8",
                 base_url: str = "https://chat.hpi-sci.de",
                 api_key: str = None):
        """
        Initialize the minutes generator

        Args:
            model: Model name for LLM
            base_url: OpenWebUI base URL
            api_key: API key for authentication
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

        print(f"Initialized Minutes Generator")
        print(f"Model: {model}")
        print(f"API Endpoint: {self.api_url}")

    def load_boundaries(self, boundaries_file: str) -> Dict[str, Dict]:
        """
        Load TOP boundaries from JSON file

        Args:
            boundaries_file: Path to boundaries JSON file

        Returns:
            Dict mapping TOP to boundary info
        """
        print(f"\nLoading TOP boundaries from {boundaries_file}...")

        with open(boundaries_file, 'r', encoding='utf-8') as f:
            boundaries = json.load(f)

        print(f"✓ Loaded boundaries for {len(boundaries)} TOPs")
        return boundaries

    def load_full_transcript(self, transcript_file: str) -> List[Dict]:
        """
        Load full transcript with all speakers

        Args:
            transcript_file: Path to transcript text file

        Returns:
            List of dicts: [{"index": int, "speaker": str, "text": str}, ...]
        """
        print(f"\nLoading full transcript from {transcript_file}...")

        # Pattern: [SPEAKER_XX]: text
        pattern = r'\[SPEAKER_(\d+)\]:\s*(.+)'

        utterances = []
        with open(transcript_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue

                match = re.match(pattern, line)
                if match:
                    speaker_id = f"SPEAKER_{match.group(1)}"
                    text = match.group(2).strip()
                    utterances.append({
                        "index": line_num,
                        "speaker": speaker_id,
                        "text": text
                    })
                else:
                    # Handle lines without speaker tags (continuation?)
                    if utterances:
                        # Append to last utterance
                        utterances[-1]["text"] += " " + line

        print(f"✓ Loaded {len(utterances)} utterances")
        print(f"  Index range: {utterances[0]['index']}-{utterances[-1]['index']}")

        return utterances

    def extract_top_segment(self,
                           utterances: List[Dict],
                           start_index: int,
                           end_index: int) -> List[Dict]:
        """
        Extract conversation segment for a specific TOP

        Args:
            utterances: Full transcript utterances
            start_index: Start index from boundaries
            end_index: End index from boundaries

        Returns:
            List of utterances in the range
        """
        segment = [u for u in utterances if start_index <= u['index'] <= end_index]
        return segment

    def format_segment_for_prompt(self, segment: List[Dict]) -> str:
        """
        Format segment as readable conversation for LLM

        Args:
            segment: List of utterances

        Returns:
            Formatted string
        """
        lines = []
        for utterance in segment:
            lines.append(f"[{utterance['speaker']}]: {utterance['text']}")
        return "\n".join(lines)

    def generate_top_summary(self, top_title: str, segment: List[Dict]) -> str:
        """
        Generate formal summary for one TOP using LLM

        Args:
            top_title: Title of the TOP
            segment: List of utterances for this TOP

        Returns:
            Formal summary text
        """
        print(f"\n  Generating summary for: {top_title[:60]}...")

        # Format conversation
        conversation = self.format_segment_for_prompt(segment)

        # Build prompt for formal minutes generation
        prompt = f"""Sie erstellen eine formale deutsche Sitzungsniederschrift (Protokoll) für einen Tagesordnungspunkt einer kommunalen Ausschusssitzung.

================================================================================
TAGESORDNUNGSPUNKT:
================================================================================
{top_title}

================================================================================
TRANSKRIPT DER DISKUSSION:
================================================================================
{conversation}

================================================================================
AUFGABE:
================================================================================
Erstellen Sie eine formale Zusammenfassung im Stil einer offiziellen Niederschrift.

ANFORDERUNGEN:

1. **Stil und Ton:**
   - Formale Verwaltungssprache (administrative German)
   - Dritte Person Erzählung (nicht wörtliche Zitate)
   - Sachlich, präzise, neutral
   - Beispiel: "Die Ausschussvorsitzende erläuterte...", "Herr X stellte dar..."

2. **Inhalt:**
   - Wesentliche Diskussionspunkte
   - Vorgestellte Berichte oder Anträge
   - Getroffene Entscheidungen
   - Durchgeführte Abstimmungen mit Ergebnissen
   - Wichtige Fragen und Antworten
   - Beschlossene Maßnahmen oder Folgeschritte

3. **Struktur:**
   - 2-5 Absätze (je nach Diskussionslänge)
   - Chronologischer Ablauf
   - Klare Übergänge zwischen Themen
   - Bei Abstimmungen: Ergebnis klar darstellen (z.B. "einstimmig beschlossen", "mit 4 Ja-Stimmen, 2 Nein-Stimmen, 1 Enthaltung")

4. **Was NICHT enthalten:**
   - Verfahrensdetails ("Mikrofon wird weitergegeben")
   - Triviale Zwischenbemerkungen
   - Technische Störungen
   - Redundante Wiederholungen

5. **Länge:**
   - Kurze TOPs (< 10 Äußerungen): 1-2 Absätze
   - Mittlere TOPs (10-50 Äußerungen): 2-3 Absätze
   - Lange TOPs (> 50 Äußerungen): 3-5 Absätze

BEISPIEL-FORMAT:

Die Ausschussvorsitzende eröffnete den Tagesordnungspunkt und erläuterte den Sachverhalt. Herr Müller vom VerkehrsConsult Dresden stellte die aktuellen Planungen zum Nahverkehrsplan 2026-2030 vor und ging dabei auf die wichtigsten Änderungen ein.

Im Anschluss entwickelte sich eine Diskussion über die vorgeschlagenen Maßnahmen. Herr Zechmann äußerte Bedenken hinsichtlich der Finanzierung und fragte nach konkreten Zeitplänen. Frau Schmidt ergänzte Aspekte zur Bürgerbeteiligung.

Der Ausschuss beschloss einstimmig, den Sachstand zur Kenntnis zu nehmen und die Verwaltung mit der weiteren Bearbeitung zu beauftragen.

================================================================================
AUSGABE:
================================================================================
Geben Sie NUR die Zusammenfassung zurück, ohne zusätzliche Erklärungen oder Formatierung.
"""

        try:
            # Prepare API request
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
                "temperature": 0.3,  # Slightly higher for more natural text
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

            response.raise_for_status()

            result = response.json()
            summary = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()

            if not summary:
                raise ValueError("Empty response from API")

            print(f"    ✓ Generated summary ({len(summary)} chars)")
            return summary

        except Exception as e:
            print(f"\n✗ ERROR generating summary: {e}")
            return f"[Fehler bei der Zusammenfassungserstellung: {e}]"

    def generate_all_minutes(self,
                            boundaries_file: str,
                            transcript_file: str,
                            output_file: str = None,
                            meeting_metadata: Dict = None):
        """
        Generate complete minutes for all TOPs

        Args:
            boundaries_file: Path to boundaries JSON
            transcript_file: Path to full transcript
            output_file: Path for output Markdown file
            meeting_metadata: Optional metadata (date, title, attendees, etc.)
        """
        print("\n" + "=" * 80)
        print("MEETING MINUTES GENERATION PIPELINE")
        print("=" * 80)

        # Auto-generate output path if not provided
        if output_file is None:
            base_dir = os.path.dirname(transcript_file)
            base_name = os.path.splitext(os.path.basename(transcript_file))[0]
            output_file = os.path.join(base_dir, f"{base_name}_minutes.md")

        # Step 1: Load boundaries
        boundaries = self.load_boundaries(boundaries_file)

        # Step 2: Load full transcript
        utterances = self.load_full_transcript(transcript_file)

        # Step 3: Generate summaries for each TOP
        print("\n" + "=" * 80)
        print("GENERATING SUMMARIES")
        print("=" * 80)

        minutes = []
        for top_title, boundary_info in boundaries.items():
            start_idx = boundary_info.get('start_index')
            end_idx = boundary_info.get('end_index')

            # Skip TOPs without valid boundaries
            if start_idx is None or end_idx is None:
                print(f"\n  ⚠️  Skipping {top_title[:60]}... (missing boundaries)")
                minutes.append({
                    "top": top_title,
                    "summary": "[Keine Zusammenfassung verfügbar - Segmentgrenzen fehlen]",
                    "start_index": start_idx,
                    "end_index": end_idx
                })
                continue

            # Extract segment
            segment = self.extract_top_segment(utterances, start_idx, end_idx)

            if not segment:
                print(f"\n  ⚠️  No utterances found for {top_title[:60]}...")
                minutes.append({
                    "top": top_title,
                    "summary": "[Keine Diskussion aufgezeichnet]",
                    "start_index": start_idx,
                    "end_index": end_idx
                })
                continue

            # Generate summary
            summary = self.generate_top_summary(top_title, segment)

            minutes.append({
                "top": top_title,
                "summary": summary,
                "start_index": start_idx,
                "end_index": end_idx,
                "utterance_count": len(segment)
            })

        # Step 4: Format as Markdown
        print("\n" + "=" * 80)
        print("FORMATTING MARKDOWN OUTPUT")
        print("=" * 80)

        markdown = self.format_as_markdown(minutes, meeting_metadata)

        # Step 5: Save to file
        print(f"\nSaving minutes to {output_file}...")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown)

        print(f"✓ Saved minutes ({len(markdown)} chars, {len(minutes)} TOPs)")

        print("\n" + "=" * 80)
        print("MINUTES GENERATION COMPLETE!")
        print("=" * 80)
        print(f"\n✓ Minutes saved to: {output_file}")

        return output_file

    def format_as_markdown(self, minutes: List[Dict], metadata: Dict = None) -> str:
        """
        Format minutes as Markdown document

        Args:
            minutes: List of TOP summaries
            metadata: Optional meeting metadata

        Returns:
            Markdown string
        """
        lines = []

        # Header
        lines.append("# Niederschrift")
        lines.append("")

        # Metadata
        if metadata:
            lines.append("## Sitzungsdaten")
            lines.append("")
            if metadata.get('title'):
                lines.append(f"**Gremium:** {metadata['title']}")
            if metadata.get('date'):
                lines.append(f"**Datum:** {metadata['date']}")
            if metadata.get('location'):
                lines.append(f"**Ort:** {metadata['location']}")
            if metadata.get('attendees'):
                lines.append(f"**Teilnehmer:** {metadata['attendees']}")
            lines.append("")
            lines.append("---")
            lines.append("")

        # TOPs
        for i, minute in enumerate(minutes, 1):
            # Extract TOP number from title if present
            top_title = minute['top']

            lines.append(f"## {top_title}")
            lines.append("")

            # Add metadata comment (hidden in display but useful for debugging)
            if minute.get('start_index') is not None:
                lines.append(f"<!-- Indices: {minute.get('start_index')}-{minute.get('end_index')} "
                           f"({minute.get('utterance_count', 0)} Äußerungen) -->")
                lines.append("")

            # Add summary
            summary = minute['summary']
            lines.append(summary)
            lines.append("")
            lines.append("---")
            lines.append("")

        # Footer
        lines.append(f"*Protokoll generiert am {datetime.now().strftime('%d.%m.%Y um %H:%M Uhr')}*")

        return "\n".join(lines)


def main():
    """Main entry point"""

    # Get API key from environment variable
    api_key = os.environ.get('OPENWEBUI_API_KEY')
    if not api_key:
        print("⚠️  OPENWEBUI_API_KEY environment variable not set!")
        print("Set it with: export OPENWEBUI_API_KEY='your-api-key-here'")
        return

    # Initialize generator
    generator = MinutesGenerator(
        model="nvidia/Llama-3.3-70B-Instruct-FP8",
        base_url="https://chat.hpi-sci.de",
        api_key=api_key
    )

    # Configure paths
    base_dir = "/home/parasmehta/Projects/tuiv/data/LK_OS/20250515_LAWI"
    boundaries_file = f"{base_dir}/20250515_LAWI_boundaries_moderator.json"
    transcript_file = f"{base_dir}/20250515_LAWI.txt"
    output_file = None  # Auto-generated

    # Optional: Add meeting metadata
    metadata = {
        "title": "Ausschuss für Landwirtschaft und Wirtschaftsförderung",
        "date": "15.05.2025",
        "location": "Landkreis Oder-Spree",
    }

    # Generate minutes
    generator.generate_all_minutes(
        boundaries_file=boundaries_file,
        transcript_file=transcript_file,
        output_file=output_file,
        meeting_metadata=metadata
    )


if __name__ == "__main__":
    main()
