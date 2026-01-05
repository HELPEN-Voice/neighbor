"""Verification Manager for Neighbor profiles.

Orchestrates parallel verification of all neighbor profiles using
Gemini Deep Research. Follows the same pattern as OpenAI deep research:
1. Read individual dr_*.json files (not merged)
2. Run verification in parallel
3. Save individual vr_*.json files
4. Return verified profiles for merging
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .verification_neighbor_person import NeighborPersonVerificationAgent
from .verification_neighbor_org import NeighborOrgVerificationAgent
from ..config.settings import settings


class NeighborVerificationManager:
    """Manages verification of neighbor profiles.

    Follows the same parallel pattern as OpenAI deep research:
    1. Read individual dr_*.json files (not merged)
    2. Run verification in parallel
    3. Save individual vr_*.json files
    4. Return verified profiles for merging
    """

    def __init__(self, output_dir: Optional[Path] = None):
        """Initialize the verification manager.

        Args:
            output_dir: Directory for verification outputs.
                       Defaults to deep_research_outputs.
        """
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = Path(__file__).parent.parent / "deep_research_outputs"

        self.person_agent = None  # Lazy init
        self.org_agent = None  # Lazy init
        self.results = {}

    def _get_person_agent(self) -> NeighborPersonVerificationAgent:
        """Get or create person verification agent."""
        if self.person_agent is None:
            self.person_agent = NeighborPersonVerificationAgent()
        return self.person_agent

    def _get_org_agent(self) -> NeighborOrgVerificationAgent:
        """Get or create organization verification agent."""
        if self.org_agent is None:
            self.org_agent = NeighborOrgVerificationAgent()
        return self.org_agent

    async def verify_all(
        self,
        dr_filepaths: List[str],
        context: Dict[str, Any],
        concurrency_limit: int = None,
    ) -> Dict[str, Any]:
        """Verify all deep research outputs in parallel.

        Args:
            dr_filepaths: List of paths to dr_*.json files from OpenAI stage
            context: Dict with county, state, city
            concurrency_limit: Max concurrent Gemini requests (default from settings)

        Returns:
            Dict with:
                - verified_profiles: List of all verified neighbor profiles
                - vr_filepaths: List of paths to saved vr_*.json files
                - stats: Verification statistics
        """
        if concurrency_limit is None:
            concurrency_limit = settings.VERIFICATION_CONCURRENCY

        print(f"\n{'=' * 70}")
        print("🔬 VERIFICATION STAGE - Gemini Deep Research (PARALLEL)")
        print(f"{'=' * 70}")
        print(f"   Files to verify: {len(dr_filepaths)}")
        print(f"   Max concurrent: {concurrency_limit}")
        print(f"   Location: {context.get('county', 'Unknown')}, {context.get('state', 'Unknown')}")
        print()

        if not dr_filepaths:
            return {
                "verified_profiles": [],
                "vr_filepaths": [],
                "stats": {
                    "files_processed": 0,
                    "files_succeeded": 0,
                    "files_failed": 0,
                    "total_profiles_verified": 0,
                },
                "errors": [],
            }

        start_time = datetime.now()

        # Run verifications in parallel using ThreadPoolExecutor
        # (Gemini API calls are synchronous but blocking)
        all_verified = []
        vr_filepaths = []
        errors = []

        with ThreadPoolExecutor(max_workers=concurrency_limit) as executor:
            future_to_file = {
                executor.submit(self._verify_single_file, fp, context): fp
                for fp in dr_filepaths
            }

            print(f"   🚀 Launched {len(future_to_file)} parallel verification tasks")
            print()

            for future in as_completed(future_to_file):
                filepath = future_to_file[future]
                filename = Path(filepath).name

                try:
                    result = future.result()
                    status = result.get("status", "unknown")

                    if status == "completed":
                        all_verified.extend(result.get("neighbors", []))
                        if result.get("saved_filepath"):
                            vr_filepaths.append(result["saved_filepath"])
                        print(f"   ✅ {filename}: Verified ({len(result.get('neighbors', []))} profiles)")
                    else:
                        errors.append({"file": filepath, "error": result.get("error", "Unknown")})
                        print(f"   ❌ {filename}: {result.get('error', 'Failed')}")

                except Exception as e:
                    errors.append({"file": filepath, "error": str(e)})
                    print(f"   ❌ {filename}: Exception - {e}")

        elapsed = (datetime.now() - start_time).total_seconds() / 60

        print(f"\n{'=' * 70}")
        print("🏁 VERIFICATION STAGE COMPLETE")
        print(f"{'=' * 70}")
        print(f"   Files processed: {len(dr_filepaths)}")
        print(f"   Files succeeded: {len(vr_filepaths)}")
        print(f"   Files failed: {len(errors)}")
        print(f"   Total profiles verified: {len(all_verified)}")
        print(f"   Total time: {elapsed:.1f} minutes")

        return {
            "verified_profiles": all_verified,
            "vr_filepaths": vr_filepaths,
            "stats": {
                "files_processed": len(dr_filepaths),
                "files_succeeded": len(vr_filepaths),
                "files_failed": len(errors),
                "total_profiles_verified": len(all_verified),
                "elapsed_minutes": elapsed,
            },
            "errors": errors,
        }

    def _verify_single_file(
        self,
        filepath: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Verify a single dr_*.json file and save as vr_*.json.

        Determines entity type from filename (dr_persons_* or dr_organizations_*).

        Args:
            filepath: Path to dr_*.json file
            context: Dict with county, state, city

        Returns:
            Dict with verification results
        """
        path = Path(filepath)

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return {"status": "failed", "error": f"Failed to read file: {e}"}

        # Determine entity type from filename or data
        entity_type = data.get("entity_type", "person")
        if "organizations" in path.name.lower():
            entity_type = "organization"
        elif "persons" in path.name.lower():
            entity_type = "person"

        profiles = data.get("neighbors", [])
        if not profiles:
            return {
                "status": "completed",
                "neighbors": [],
                "saved_filepath": None,
                "note": "No profiles in file",
            }

        # Select appropriate agent
        agent = self._get_person_agent() if entity_type == "person" else self._get_org_agent()

        # Run verification
        verified = agent.verify_batch(
            profiles=profiles,
            context=context,
            entity_type=entity_type,
        )

        if verified.get("status") != "completed":
            return verified

        # Save verified output with vr_ prefix
        vr_filename = path.name.replace("dr_", "vr_")
        vr_path = self.output_dir / vr_filename

        # Save thinking summaries to separate markdown file (not JSON serializable)
        metadata = verified.get("metadata", {})
        thinking_summaries = metadata.get("thinking_summaries", [])
        if thinking_summaries:
            thinking_path = vr_path.with_suffix(".thinking.md")
            thinking_content = f"# Thinking Summaries for {path.name}\n\n"
            for i, thought in enumerate(thinking_summaries, 1):
                thinking_content += f"## Thought {i}\n\n{thought}\n\n---\n\n"
            with open(thinking_path, "w", encoding="utf-8") as f:
                f.write(thinking_content)
            print(f"   🧠 Saved thinking summaries: {thinking_path.name}")

        # Save JSON without thinking_summaries (they're not serializable)
        safe_metadata = {
            "entity_type": metadata.get("entity_type"),
            "profiles_input": metadata.get("profiles_input"),
            "profiles_output": metadata.get("profiles_output"),
            "total_tokens": metadata.get("total_tokens"),
            "thinking_count": len(thinking_summaries),
        }

        save_data = {
            "timestamp": datetime.now().isoformat(),
            "source_file": str(path),
            "entity_type": entity_type,
            "location_context": context,
            "neighbors": verified.get("neighbors", []),
            "verification_metadata": safe_metadata,
        }

        try:
            with open(vr_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
            print(f"   💾 Saved verified output to: {vr_path.name}")
        except Exception as e:
            return {
                "status": "failed",
                "error": f"Failed to save verified file: {e}",
                "neighbors": verified.get("neighbors", []),
            }

        return {
            "status": "completed",
            "neighbors": verified.get("neighbors", []),
            "saved_filepath": str(vr_path),
            "metadata": verified.get("metadata", {}),
        }

    def verify_single_agent(
        self,
        profiles: List[Dict[str, Any]],
        context: Dict[str, Any],
        entity_type: str = "person",
    ) -> Dict[str, Any]:
        """Verify a single batch directly (without file I/O).

        Useful for testing or when profiles are already in memory.

        Args:
            profiles: List of neighbor profile dicts
            context: Dict with county, state, city
            entity_type: "person" or "organization"

        Returns:
            Dict with verified profiles and metadata
        """
        agent = self._get_person_agent() if entity_type == "person" else self._get_org_agent()
        return agent.verify_batch(profiles, context, entity_type)


def get_vr_files_for_run(dr_files: List[str]) -> List[str]:
    """Convert dr_* paths to vr_* paths.

    Args:
        dr_files: List of dr_*.json file paths

    Returns:
        List of corresponding vr_*.json paths
    """
    return [f.replace("/dr_", "/vr_") for f in dr_files]


def load_verified_profiles(vr_files: List[str]) -> List[Dict[str, Any]]:
    """Load and combine all verified profiles from vr_*.json files.

    Args:
        vr_files: List of vr_*.json file paths

    Returns:
        List of all verified neighbor profiles
    """
    all_profiles = []
    for filepath in vr_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            all_profiles.extend(data.get("neighbors", []))
        except Exception as e:
            print(f"   ⚠️ Failed to load {filepath}: {e}")
    return all_profiles


# CLI for testing
if __name__ == "__main__":
    import argparse
    import os

    def load_env():
        """Load environment from .env file."""
        env_path = Path(__file__).parent.parent.parent.parent / ".env"
        if env_path.exists():
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        value = value.strip('"').strip("'")
                        os.environ[key] = value
            print(f"✅ Loaded environment from {env_path}")

    parser = argparse.ArgumentParser(description="Test neighbor verification")
    parser.add_argument("--file", "-f", help="Specific dr_*.json file to verify")
    parser.add_argument("--county", "-c", default="Unknown", help="County name")
    parser.add_argument("--state", "-s", default="Unknown", help="State abbreviation")
    args = parser.parse_args()

    load_env()

    context = {
        "county": args.county,
        "state": args.state,
    }

    if args.file:
        manager = NeighborVerificationManager()
        result = manager._verify_single_file(args.file, context)
        print(f"\nResult: {result.get('status')}")
        if result.get("neighbors"):
            print(f"Verified {len(result['neighbors'])} profiles")
    else:
        print("Usage: python verification_manager_neighbor.py --file dr_persons_*.json --county 'County' --state 'ST'")
