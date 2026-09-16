"""Compatibility imports for the pre-registry teacher module name.

New code should import :mod:`mouse_control.teacher_registry`. Keeping this
shim avoids breaking guided discovery or third-party development code while
ensuring teacher architecture no longer lives behind a "backend" name.
"""

from .teacher_registry import (
    HidppTeacher,
    NativeRazerTeacher,
    TeacherAdapter,
    TeacherProvenance,
    TeacherRegistry,
    TeacherState,
    TeacherTrust,
    TeacherValue,
    read_teacher_labels,
    read_teacher_state,
)

# Historical name used by the first guided-learning experiment.
read_backend_teacher_state = read_teacher_labels

__all__ = [
    "HidppTeacher",
    "NativeRazerTeacher",
    "TeacherAdapter",
    "TeacherProvenance",
    "TeacherRegistry",
    "TeacherState",
    "TeacherTrust",
    "TeacherValue",
    "read_backend_teacher_state",
    "read_teacher_labels",
    "read_teacher_state",
]
