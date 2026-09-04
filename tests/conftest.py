"""Shared pytest fixtures.

Most Qt-widget test modules in this suite share a single process-wide
QApplication instance (there can only ever be one). Across ~180 tests that
each create dialogs/widgets/pixmaps under the offscreen platform plugin,
never explicitly closing/destroying them accumulates dangling native
resources in that shared QApplication - this previously caused an
intermittent, hard-to-reproduce segfault deep inside PySide6 (observed when
running the full suite, but never when running the affected test file in
isolation) once enough prior tests had run. Draining the event queue and
forcing a GC pass after every single test - not just Qt ones, it's a no-op
and effectively free for non-Qt tests - is the standard mitigation for this
whole class of PySide6/PyQt test-suite instability."""
from __future__ import annotations

import gc

import pytest


@pytest.fixture(autouse=True)
def _drain_qt_events_and_gc():
    yield
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass
    gc.collect()
