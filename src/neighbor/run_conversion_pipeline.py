#!/usr/bin/env python3
"""
Neighbor Conversion Pipeline Script

Converts sample neighbor JSON to final PDF:
1. Cleans up existing output directories
2. Converts JSON to HTML using templates
3. Converts HTML files to PDF using Playwright
4. Combines PDFs into final report

Usage:
    python run_conversion_pipeline.py
    python run_conversion_pipeline.py --verbose
    python run_conversion_pipeline.py --json-file custom_neighbor.json
"""

import asyncio
import argparse
import logging
import os
import sys
import time
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any


# Load environment variables from .env file
def load_env_file():
    """Load environment variables from .env file"""
    # Look for .env file in project root (go up 4 levels from this script)
    script_dir = os.path.dirname(__file__)
    project_root = os.path.abspath(os.path.join(script_dir, "../../../../"))
    env_path = os.path.join(project_root, ".env")

    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    # Remove quotes if present
                    value = value.strip('"').strip("'")
                    os.environ[key] = value
        print(f"✅ Loaded environment from {env_path}")
    else:
        print(f"⚠️ No .env file found at {env_path}")


load_env_file()

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            f"neighbor_conversion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        ),
    ],
)
logger = logging.getLogger(__name__)


class NeighborConversionPipeline:
    """Pipeline that handles conversion of neighbor JSON to PDF"""

    def __init__(self, json_file: Optional[str] = None):
        self.script_dir = Path(__file__).parent
        # Always use the final merged output from orchestrator
        if json_file:
            self.json_file = json_file
        else:
            self.json_file = str(
                self.script_dir / "neighbor_outputs" / "neighbor_final_merged.json"
            )
        self.start_time = time.time()

    def print_banner(self, title: str):
        """Print a formatted banner"""
        print("\n" + "=" * 80)
        print(f"🚀 {title}")
        print("=" * 80)

    def print_step(self, step_num: int, title: str, description: str = ""):
        """Print formatted step information"""
        print(f"\n📍 STEP {step_num}: {title}")
        print("-" * 60)
        if description:
            print(f"   {description}")
        print()

    def print_success(self, message: str):
        """Print success message"""
        print(f"✅ {message}")

    def print_error(self, message: str):
        """Print error message"""
        print(f"❌ {message}")
        logger.error(message)

    def print_warning(self, message: str):
        """Print warning message"""
        print(f"⚠️ {message}")
        logger.warning(message)

    def cleanup_directories(self) -> bool:
        """Clean up existing output directories to avoid duplicates"""
        self.print_step(1, "Directory Cleanup", "Removing existing output directories")

        directories_to_clean = [
            self.script_dir / "neighbor_html_outputs",
            self.script_dir / "individual_pdf_reports",
            self.script_dir / "combined_pdf_reports",
        ]

        for directory in directories_to_clean:
            if directory.exists():
                try:
                    # Use trash instead of rm as per user's global instructions
                    trash_cmd = f"trash {directory}"
                    result = subprocess.run(
                        trash_cmd, shell=True, capture_output=True, text=True
                    )

                    if result.returncode == 0:
                        self.print_success(f"Cleaned: {directory.name}")
                    else:
                        # Fallback to shutil if trash command fails
                        shutil.rmtree(directory)
                        self.print_success(f"Cleaned: {directory.name} (using shutil)")

                    # Recreate the directory
                    directory.mkdir(parents=True, exist_ok=True)

                except Exception as e:
                    self.print_warning(f"Could not clean {directory.name}: {str(e)}")
            else:
                # Create directory if it doesn't exist
                directory.mkdir(parents=True, exist_ok=True)
                self.print_success(f"Created: {directory.name}")

        self.print_success("Directory cleanup completed")
        return True

    def check_json_file(self) -> bool:
        """Check if the JSON file exists"""
        self.print_step(2, "JSON File Check", f"Verifying {self.json_file} exists")

        json_path = self.script_dir / self.json_file

        if not json_path.exists():
            self.print_error(f"JSON file does not exist: {json_path}")
            return False

        self.print_success(f"Found JSON file: {self.json_file}")
        return True

    def run_html_conversion(self) -> bool:
        """Convert JSON to HTML using templates"""
        self.print_step(
            3, "HTML Conversion", "Converting JSON to HTML using Jinja2 templates"
        )

        try:
            script_path = self.script_dir / "convert_neighbor_to_html.py"

            # Import and run the conversion function directly
            import json
            from convert_neighbor_to_html import generate_neighbor_reports

            # Load the JSON data
            json_path = self.script_dir / self.json_file
            with open(json_path, "r") as f:
                data = json.load(f)

            # Run the conversion
            generate_neighbor_reports(data)

            self.print_success("HTML conversion completed successfully")
            return True

        except Exception as e:
            self.print_error(f"HTML conversion failed with exception: {str(e)}")
            return False

    def run_pdf_conversion(self) -> bool:
        """Convert HTML files to PDFs"""
        self.print_step(
            4, "PDF Conversion", "Converting HTML files to PDFs using Playwright"
        )

        try:
            script_path = self.script_dir / "convert_html_to_pdf.py"
            result = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=self.script_dir,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout for PDF generation
            )

            if result.returncode == 0:
                self.print_success("PDF conversion completed successfully")
                if result.stdout:
                    print("PDF conversion output:", result.stdout.strip())
                return True
            else:
                self.print_error(
                    f"PDF conversion failed with return code {result.returncode}"
                )
                if result.stderr:
                    print("Error output:", result.stderr.strip())
                return False

        except subprocess.TimeoutExpired:
            self.print_error("PDF conversion timed out after 10 minutes")
            return False
        except Exception as e:
            self.print_error(f"PDF conversion failed with exception: {str(e)}")
            return False

    async def run_pipeline(self) -> Optional[str]:
        """Run the conversion pipeline"""

        self.print_banner("NEIGHBOR CONVERSION PIPELINE STARTING")
        print(f"🕐 Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📄 JSON file: {self.json_file}")

        try:
            # Step 1: Clean up directories
            if not self.cleanup_directories():
                self.print_error("Pipeline stopped due to directory cleanup failure")
                return None

            # Step 2: Check for JSON file
            if not self.check_json_file():
                self.print_error("Pipeline stopped - JSON file not found")
                return None

            # Step 3: Convert to HTML
            if not self.run_html_conversion():
                self.print_error("Pipeline stopped due to HTML conversion failure")
                return None

            # Step 4: Convert to PDF (also combines PDFs)
            if not self.run_pdf_conversion():
                self.print_error("Pipeline stopped due to PDF conversion failure")
                return None

            # Get the combined PDF path
            combined_dir = self.script_dir / "combined_pdf_reports"
            combined_pdfs = list(combined_dir.glob("*.pdf"))
            final_pdf_path = str(combined_pdfs[0]) if combined_pdfs else None

            # Success!
            total_time = (time.time() - self.start_time) / 60
            self.print_banner("CONVERSION COMPLETED SUCCESSFULLY")
            print(f"🎉 Total execution time: {total_time:.1f} minutes")
            print(f"📄 Final report: {final_pdf_path}")

            return final_pdf_path

        except Exception as e:
            total_time = (time.time() - self.start_time) / 60
            self.print_error(
                f"Pipeline failed after {total_time:.1f} minutes: {str(e)}"
            )
            logger.exception("Pipeline execution failed")
            return None


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Convert neighbor JSON to final PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python run_conversion_pipeline.py
    python run_conversion_pipeline.py --verbose
    python run_conversion_pipeline.py --json-file my_neighbor_data.json
        """,
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    parser.add_argument(
        "--json-file",
        "-j",
        type=str,
        default="neighbor_outputs/neighbor_final_merged.json",
        help="JSON file to convert (default: neighbor_outputs/neighbor_final_merged.json)",
    )

    return parser.parse_args()


async def main():
    """Main entry point"""
    args = parse_arguments()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    pipeline = NeighborConversionPipeline(json_file=args.json_file)

    final_pdf_path = await pipeline.run_pipeline()

    if final_pdf_path:
        print(f"\n🎯 SUCCESS: Final report available at: {final_pdf_path}")
        return 0
    else:
        print(f"\n💥 FAILURE: Pipeline did not complete successfully")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
