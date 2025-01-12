import click
import re
import os
from pathlib import Path
from pyfzf import FzfPrompt
from pyzotero import zotero
from datetime import datetime, timezone

# Unset HTTP proxies
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)


def sanitize_filename(title):
    """Convert title to valid filename"""
    # Replace colons with space-dash
    filename = title.replace(":", " -")
    # Remove other problematic characters
    filename = re.sub(r'[<>"/\\|?*]', "", filename)
    # Remove multiple dashes
    filename = re.sub(r"-+", "-", filename)
    # Remove leading/trailing dashes and spaces
    filename = filename.strip("- ")
    return f"{filename}.md"


# Mapping from Zotero fields to frontmatter keys
FRONTMATTER_MAPPING = {
    "key": "zotero_key",
    "data.itemType": "item_type",
    "data.title": "title",
    "data.abstractNote": "abstract",
    "data.url": "url",
    "data.DOI": "doi",
    "data.publicationTitle": "publication",
    "data.dateAdded": "add_date",
    "data.creators": "authors",
    "data.tags": "tags",
}


def get_nested_value(item, path):
    """Get value from nested dictionary using dot notation path"""
    current = item
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def create_markdown_content(item, metadata):
    """Create markdown content with frontmatter"""
    frontmatter = ["---"]

    # Add citation key from metadata if it exists
    citation_key = metadata.get("Citation Key", "")
    if citation_key:
        frontmatter.append(f'citation_key: "{citation_key}"')

    # Add mapped fields from item
    for source_path, target_key in FRONTMATTER_MAPPING.items():
        value = get_nested_value(item, source_path)
        if value:  # Only add if value exists and is not empty
            if target_key == "authors":
                # Extract author names and create a list
                authors = [
                    f"{creator['firstName']} {creator['lastName']}"
                    for creator in value
                    if creator["creatorType"] == "author"
                ]
                if authors:
                    frontmatter.append(f"authors: {authors}")
            elif target_key == "tags":
                # Extract tag names and create a list, always include 'literature'
                tags = [t["tag"] for t in value]
                tags.append("literature")
                frontmatter.append(f"tags: {tags}")
            else:
                # Handle other fields as before
                frontmatter.append(f'{target_key}: "{value}"')

    frontmatter.extend(["---", "", f'# {item["data"]["title"]}', ""])

    return "\n".join(frontmatter)


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


class AliasedGroup(click.Group):
    def get_command(self, ctx, cmd_name):
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv
        matches = [x for x in self.list_commands(ctx) if x.startswith(cmd_name)]
        if not matches:
            return None
        elif len(matches) == 1:
            return click.Group.get_command(self, ctx, matches[0])
        ctx.fail(f"Too many matches: {', '.join(sorted(matches))}")

    def resolve_command(self, ctx, args):
        # always return the full command name
        _, cmd, args = super().resolve_command(ctx, args)
        return cmd.name, cmd, args


@click.group(invoke_without_command=True, cls=AliasedGroup)
@click.pass_context
def cli(ctx):
    """Synchronize Zotero literature with your knowledge vault"""
    if ctx.invoked_subcommand is None:
        ctx.invoke(today)


@cli.command()
def today():
    """Create markdown files for items added today"""
    zot = zotero.Zotero(library_id=0, library_type="user", api_key="", local=True)
    zot.add_parameters(limit=50, sort="dateAdded", direction="desc")
    items = zot.items()

    # Ensure the Literature_Notes directory exists
    notes_dir = Path.home() / "Documents" / "Silverbullet" / "Literature_Note"
    notes_dir.mkdir(parents=True, exist_ok=True)

    for item in items:
        # Skip attachments
        if item["data"]["itemType"] == "attachment":
            continue

        date_added = item["data"]["dateAdded"]
        if is_added_today(date_added):
            extra_field = item["data"].get("extra", "")
            metadata = parse_extra(extra_field)

            # Create filename from title
            title = item["data"].get("title", "Untitled")
            filename = sanitize_filename(title)
            filepath = notes_dir / filename

            # Only create file if it doesn't exist
            if filepath.exists():
                continue

            # Create markdown content
            content = create_markdown_content(item, metadata)

            # Write to file
            filepath.write_text(content)
            print(f"Created: {filepath}")


@cli.command(name="search")
def search():
    """Search through Zotero items using fzf"""
    zot = zotero.Zotero(library_id=0, library_type="user", api_key="", local=True)
    zot.add_parameters(limit=0, sort="dateAdded", direction="desc")
    items = zot.items()

    # Filter out attachments and prepare titles for fzf
    titles = []
    title_to_item = {}
    for item in items:
        if item["data"]["itemType"] == "attachment":
            continue
        title = item["data"].get("title", "Untitled")
        titles.append(title)
        title_to_item[title] = item

    # Use fzf to select a title
    fzf = FzfPrompt()
    try:
        selected = fzf.prompt(titles)[0]

        # Get the selected item and create markdown
        item = title_to_item[selected]
        extra_field = item["data"].get("extra", "")
        metadata = parse_extra(extra_field)

        # Create filename and path
        filename = sanitize_filename(selected)
        notes_dir = Path.home() / "Documents" / "Silverbullet" / "Literature_Note"
        filepath = notes_dir / filename

        # Create markdown content and write to file
        content = create_markdown_content(item, metadata)
        filepath.write_text(content)
        print(f"Created: {filepath}")

    except (IndexError, KeyError):
        print("No selection made")


if __name__ == "__main__":
    cli()
