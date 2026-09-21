"""Minimal command-line entry point for an Orca Router connection check."""

import argparse
import sys

from pydantic import ValidationError

from ai_hackathon_team_a.clients import OrcaClient, OrcaClientError
from ai_hackathon_team_a.config import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send one prompt to Orca Router.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Prompt to send. Omit it to enter the prompt interactively.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prompt = args.prompt if args.prompt is not None else input("Prompt: ")

    try:
        settings = get_settings()
        result = OrcaClient(settings).generate(prompt)
    except ValidationError:
        print(
            "Configuration error: copy .env.example to .env and set a valid ORCAROUTER_API_KEY.",
            file=sys.stderr,
        )
        return 2
    except (ValueError, OrcaClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(result)
    return 0
