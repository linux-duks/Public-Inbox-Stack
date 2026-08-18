# TODO: read yaml config file example.configs.yaml
#
import yaml

def load_config(file_path="config.yaml"):
    """Safely opens and loads a YAML configuration file."""
    try:
        with open(file_path, "r") as stream:
            return yaml.safe_load(stream)
    except FileNotFoundError:
        print(f"Error: The file {file_path} was not found.")
        return None
    except yaml.YAMLError as exc:
        print(f"Error parsing YAML file: {exc}")
        return None
