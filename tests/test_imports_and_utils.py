from pathlib import Path

from mtbl_valuations import config, utils
from mtbl_valuations.config import paths as config_paths
from mtbl_valuations.utils import constants as utils_constants


def test_imports_expose_paths():
    assert isinstance(config.RESOURCES_PATH, Path)
    assert config.RESOURCES_PATH == config_paths.RESOURCES_PATH
    assert utils.RESOURCES_PATH == config_paths.RESOURCES_PATH
    assert utils_constants.LOAD_OUTPUT_DIR == config.LOAD_OUTPUT_DIR
