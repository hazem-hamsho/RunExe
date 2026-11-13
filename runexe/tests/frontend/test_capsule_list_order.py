from types import SimpleNamespace

import pytest
from gi.repository import Gio

Gio.resources_register(Gio.Resource.load("/app/share/runexe/runexe.gresource"))

from runexe.frontend.views.list import (  # noqa: E402
    RunExeCapsuleRow,
    CapsuleView,
    _capsule_order_id,
    _ordered_capsules,
    _replace_group_order,
)


def _config(path, environment="Application"):
    return SimpleNamespace(Path=path, Name=path, Environment=environment)


def test_default_capsule_order_uses_display_names_without_case():
    alpha = _config("old-z-folder")
    alpha.Name = "alpha"
    beta = _config("old-a-folder")
    beta.Name = "Beta"

    assert _ordered_capsules([beta, alpha], []) == [alpha, beta]
    alpha.Name = "Zulu"
    assert _ordered_capsules([alpha, beta], []) == [beta, alpha]


def test_ordered_capsules_keeps_new_capsules_at_the_end():
    alpha = _config("Alpha")
    beta = _config("Beta")
    gamma = _config("Gamma")

    ordered = _ordered_capsules(
        [alpha, beta, gamma],
        [_capsule_order_id(beta), _capsule_order_id(alpha)],
    )

    assert [config.Path for config in ordered] == ["Beta", "Alpha", "Gamma"]


def test_replace_group_order_keeps_unavailable_and_other_group_entries():
    configured = [
        "capsule:Alpha",
        "capsule:Unavailable",
        "steam:/steam/one",
        "capsule:Beta",
    ]

    result = _replace_group_order(
        configured,
        ["capsule:Alpha", "capsule:Beta", "capsule:Gamma"],
        ["capsule:Gamma", "capsule:Beta", "capsule:Alpha"],
    )

    assert result == [
        "capsule:Gamma",
        "capsule:Unavailable",
        "steam:/steam/one",
        "capsule:Beta",
        "capsule:Alpha",
    ]


def test_capsule_row_refreshes_programs_after_executable_finishes():
    calls = []
    config = _config("Test")
    row = SimpleNamespace(
        config=config,
        manager=SimpleNamespace(
            get_programs=lambda called_config, **kwargs: calls.append(
                (called_config, kwargs)
            )
        ),
    )

    RunExeCapsuleRow._on_executable_finished(row)

    assert calls == [(config, {"force_update": True})]


@pytest.mark.parametrize(
    ("titles", "terms", "expected"),
    [
        ([], "", (False, True, False)),
        (["Alpha"], "alpha", (True, False, False)),
        (["Alpha"], "missing", (False, False, True)),
    ],
)
def test_capsule_list_empty_state(titles, terms, expected):
    visible = {}

    def widget(name):
        return SimpleNamespace(
            set_visible=lambda value: visible.__setitem__(name, value)
        )

    view = SimpleNamespace(
        _CapsuleView__capsules={
            title: SimpleNamespace(get_title=lambda title=title: title)
            for title in titles
        },
        _CapsuleView__filter_capsules=CapsuleView._CapsuleView__filter_capsules,
        pref_page=widget("list"),
        capsule_status=widget("empty"),
        no_capsules_found=widget("no-results"),
    )

    CapsuleView._CapsuleView__update_empty_state(view, terms)

    assert (
        visible["list"],
        visible["empty"],
        visible["no-results"],
    ) == expected


def test_search_updates_filters_and_empty_state():
    filters = []
    empty_states = []
    filter_capsules = CapsuleView._CapsuleView__filter_capsules
    capsule_list = SimpleNamespace(
        set_filter_func=lambda callback, terms: filters.append((callback, terms))
    )
    view = SimpleNamespace(
        _CapsuleView__filter_capsules=filter_capsules,
        _CapsuleView__update_empty_state=empty_states.append,
        list_capsules=capsule_list,
    )

    CapsuleView._CapsuleView__search_capsules(
        view, SimpleNamespace(get_text=lambda: "missing")
    )

    assert filters == [(filter_capsules, "missing")]
    assert empty_states == ["missing"]
