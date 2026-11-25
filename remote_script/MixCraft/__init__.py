# -*- coding: utf-8 -*-
"""MixCraft Remote Script package (Live 10, Python 2.7).

Copy this whole ``MixCraft`` folder into the Live User Library under
``Remote Scripts/`` and select "MixCraft" as a control surface in
Live's Link/MIDI preferences, with the DDJ-RB as its input/output.
See ../SETUP.md for the full setup and the template Set it expects.
"""
try:
    from .MixCraft import MixCraft
except (ImportError, ValueError):
    from MixCraft.MixCraft import MixCraft


def create_instance(c_instance):
    """Entry point Live calls when the surface is selected."""
    return MixCraft(c_instance)
