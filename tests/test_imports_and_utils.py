import logging
from pathlib import Path

from mtbl_valuations import config, utils
from mtbl_valuations.config import paths as config_paths
from mtbl_valuations.utils import constants as utils_constants
from mtbl_valuations.utils.log import PACKAGE_LOGGER, configure_logging, get_logger


def test_imports_expose_paths():
    assert isinstance(config.RESOURCES_PATH, Path)
    assert config.RESOURCES_PATH == config_paths.RESOURCES_PATH
    assert utils.RESOURCES_PATH == config_paths.RESOURCES_PATH
    assert utils_constants.LOAD_OUTPUT_DIR == config.LOAD_OUTPUT_DIR


def test_configure_logging_verbosity_maps_to_level():
    """-v count maps to levels; the explicit log_level overrides it."""
    assert configure_logging(verbosity=0).level == logging.WARNING
    assert configure_logging(verbosity=1).level == logging.INFO
    assert configure_logging(verbosity=2).level == logging.DEBUG
    # 3+ saturates at DEBUG rather than raising
    assert configure_logging(verbosity=5).level == logging.DEBUG
    # explicit level wins over the verbosity count
    assert configure_logging(verbosity=0, log_level="DEBUG").level == logging.DEBUG
    assert configure_logging(verbosity=2, log_level="warning").level == logging.WARNING


def test_configure_logging_is_idempotent():
    """Repeated calls must not stack duplicate handlers."""
    configure_logging(verbosity=1)
    configure_logging(verbosity=2)
    logger = logging.getLogger(PACKAGE_LOGGER)
    assert len(logger.handlers) == 1


def test_get_logger_returns_package_child():
    child = get_logger("mtbl_valuations.io.loader")
    assert child.name == "mtbl_valuations.io.loader"
    # child loggers inherit the package logger's configured level
    configure_logging(verbosity=1)
    assert child.getEffectiveLevel() == logging.INFO
