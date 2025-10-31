import asyncio, argparse
from pathlib import Path
from playwright.async_api import async_playwright

WIDTH_IN, HEIGHT_IN = "10in", "5.625in"  # Google Slides 16:9

BASE = Path(__file__).resolve().parent
HTML_DIR = BASE / "neighbor_html_outputs"
PDF_DIR = BASE / "individual_pdf_reports"
COMBINED_DIR = BASE / "combined_pdf_reports"


async def render(html_path: Path, out_pdf: Path):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(html_path.as_uri(), wait_until="load")
        await page.emulate_media(media="print")
        await page.pdf(
            path=str(out_pdf),
            width=WIDTH_IN,
            height=HEIGHT_IN,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            print_background=True,
            prefer_css_page_size=True,
            display_header_footer=False,  # keep visual identical to your templates
        )
        await context.close()
        await browser.close()


def combine_pdfs(location=None, date=None, run_id=None):
    """Combine individual PDFs into a single report.

    Args:
        location: Location string (e.g., "Bennett, NE")
        date: Date string in YYYY-MM-DD format
        run_id: Unique run identifier
    """
    try:
        from pypdf import PdfReader, PdfWriter
        from datetime import datetime
        import json

        COMBINED_DIR.mkdir(parents=True, exist_ok=True)
        writer = PdfWriter()

        # PDFs to combine in order
        pdf_files = [
            "neighbor-title-page-playwright.pdf",
            "neighbor-parameters-playwright.pdf",
            "neighbor-deep-dive.pdf",
        ]

        combined_count = 0
        for pdf_name in pdf_files:
            pdf_path = PDF_DIR / pdf_name
            if pdf_path.exists():
                reader = PdfReader(str(pdf_path))
                for page in reader.pages:
                    writer.add_page(page)
                combined_count += 1
                print(f"  Added {pdf_name} to combined PDF")

        if combined_count > 0:
            # Try to load metadata from JSON if not provided
            if not all([location, date, run_id]):
                json_path = BASE / "neighbor_outputs" / "neighbor_final_merged.json"
                if json_path.exists():
                    try:
                        with open(json_path) as f:
                            data = json.load(f)
                            if not location:
                                city = data.get("city", "")
                                state = data.get("state", "")
                                location = (
                                    f"{city}, {state}" if city and state else "Unknown"
                                )
                            if not date:
                                date = datetime.now().strftime("%Y-%m-%d")
                            if not run_id:
                                run_id = data.get("run_id", "")
                    except Exception as e:
                        print(f"  ⚠ Could not load metadata from JSON: {e}")

            # Construct filename: "Location YYYY-MM-DD run_id.pdf"
            if location and date and run_id:
                filename = f"{location} {date} {run_id}.pdf"
            else:
                filename = "neighbor_report.pdf"

            combined_path = COMBINED_DIR / filename
            with open(combined_path, "wb") as fp:
                writer.write(fp)
            print(f"✓ Combined {combined_count} PDFs into {combined_path}")
            return str(combined_path)
        else:
            print("⚠ No PDFs found to combine")
            return None

    except ImportError:
        print("⚠ pypdf not installed, skipping PDF combination")
        return None
    except Exception as e:
        print(f"⚠ Failed to combine PDFs: {e}")
        return None


async def main(pattern: str):
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(HTML_DIR.glob(pattern))
    if not files:
        raise SystemExit(f"No HTML files found in {HTML_DIR} with pattern {pattern}")

    # Convert HTML to individual PDFs
    for f in files:
        out_pdf = PDF_DIR / (f.stem + ".pdf")
        await render(f, out_pdf)
        print(f"✓ Converted {f.name} -> {out_pdf.name}")

    # Combine PDFs into single report
    print("\nCombining PDFs...")
    combined_path = combine_pdfs()

    if combined_path:
        print(f"\n✅ Final report: {combined_path}")
    else:
        print(f"\n✅ Individual PDFs saved in: {PDF_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="*.html")
    args = ap.parse_args()
    asyncio.run(main(args.pattern))
