"""Copy a few public DICOM test images into samples/ for trying the viewer.

The files come from pydicom's test data (downloaded on first use). They are
real images with test identities, and none comes with a confirmed diagnosis.

    python scripts/fetch_samples.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pydicom.data import get_testdata_file

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

# pydicom test file -> name in samples/
FILES = {
    "RG1_UNCR.dcm": "chest-xray.dcm",
    "RG1_J2KR.dcm": "chest-xray-jpeg2000.dcm",
    "RG3_UNCR.dcm": "extremity-xray.dcm",
    "RG3_J2KR.dcm": "extremity-xray-jpeg2000.dcm",
    "693_UNCR.dcm": "ct-slice.dcm",
    "MR2_UNCR.dcm": "mr-slice.dcm",
    "US1_UNCR.dcm": "ultrasound-color.dcm",
}


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    for source, name in FILES.items():
        path = get_testdata_file(source)
        if path is None:
            print(f"could not download {source}")
            continue
        shutil.copyfile(path, SAMPLES / name)
        print(f"samples/{name}")


if __name__ == "__main__":
    main()
