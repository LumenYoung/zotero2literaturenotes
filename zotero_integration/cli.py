import click
from pyzotero import zotero
from datetime import datetime, timezone
import re


def parse_extra(extra_text):
    """Parse the extra field into a dictionary of metadata"""
    if not extra_text:
        return {}

    metadata = {}
    for line in extra_text.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    return metadata


def is_added_today(date_str):
    """Check if the given date string is from today"""
    if not date_str:
        return False

    item_date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    today = datetime.now(timezone.utc)

    return (
        item_date.year == today.year
        and item_date.month == today.month
        and item_date.day == today.day
    )


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """Synchronize Zotero literature with your knowledge vault"""
    if ctx.invoked_subcommand is None:
        ctx.invoke(today)


@cli.command()
def today():
    """Show items added today"""
    zot = zotero.Zotero(library_id=0, library_type="user", api_key="", local=True)
    items = zot.items()

    for item in items:
        date_added = item["data"].get("dateAdded")
        if is_added_today(date_added):
            extra_field = item["data"].get("extra", "")
            metadata = parse_extra(extra_field)

            # Print the item key and citation key if available
            citation_key = metadata.get("Citation Key", "No citation key")
            print(f"{item['key']}: {citation_key}")
            # Print other metadata if present
            for key, value in metadata.items():
                if key != "Citation Key":
                    print(f"  {key}: {value}")
            print()


if __name__ == "__main__":
    cli()
