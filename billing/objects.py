def get_field(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except KeyError, TypeError:
        return default
