from importlib.metadata import PackageNotFoundError, version

SOFTWARE_NAME = "panels"

try:
    SOFTWARE_VERSION = version(SOFTWARE_NAME)
except PackageNotFoundError:
    SOFTWARE_VERSION = "0.0.0"
