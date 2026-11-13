"""Unit tests for the command-line interface."""

import shlex

from runexe.frontend.cli.utils import serialize_arguments


def test_serialize_arguments_preserves_argument_boundaries():
    assert (
        serialize_arguments(
            ["/home/user/My Documents/report.txt", "--view", "single'quote"]
        )
        == "'/home/user/My Documents/report.txt' --view 'single'\"'\"'quote'"
    )


def test_serialize_arguments_preserves_portal_document_path():
    document = "/run/user/1000/doc/abc123/My Document.txt"

    assert serialize_arguments([document]) == shlex.quote(document)
