"""Deterministic decimal bucket boundaries shared by both sources."""
from decimal import Decimal, ROUND_CEILING


def adjusted(value, typ, path, profile):
    if typ != 'number':
        return value
    number = Decimal(value)
    config = profile.get('bucket_config', {}).get(path, {})
    width = Decimal(str(config.get('width', profile.get('bucket_width', .2))))
    if not width.is_finite() or width <= 0:
        raise ValueError('Bucket width must be positive and finite')
    upper = (number / width).to_integral_value(rounding=ROUND_CEILING) * width
    lower = upper - width

    boundaries = set()
    scale_min = Decimal(str(config['scale_min'])) if 'scale_min' in config else None
    scale_max = Decimal(str(config['scale_max'])) if 'scale_max' in config else None
    for edge in (scale_min, scale_max):
        if edge is not None:
            if not edge.is_finite():
                raise ValueError('Invalid bucket scale')
            boundaries.add(edge)
    if scale_min is not None and scale_max is not None and scale_min >= scale_max:
        raise ValueError('Invalid bucket scale')
    if 'threshold' in config:
        threshold = Decimal(str(config['threshold']))
        radius = Decimal(str(config.get('radius', .1)))
        if not radius.is_finite() or radius <= 0 or not threshold.is_finite():
            raise ValueError('Invalid threshold bucket configuration')
        zone_min, zone_max = threshold - radius, threshold + radius
        if scale_min is not None:
            zone_min = max(scale_min, zone_min)
        if scale_max is not None:
            zone_max = min(scale_max, zone_max)
        zone = sorted(x for x in {zone_min, threshold, zone_max}
                      if (scale_min is None or x >= scale_min) and (scale_max is None or x <= scale_max))
        for start, end in zip(zone, zone[1:]):
            if start < number <= end:
                lower, upper = start, end
                break
        boundaries.update(zone)
    for item in profile.get('buckets', {}).get(path, []):
        start, end = Decimal(str(item['min'])), Decimal(str(item['max']))
        if start < number <= end:
            return item['label']
        boundaries.update((start, end))
    lower = max((e for e in boundaries if lower < e < number), default=lower)
    upper = min((e for e in boundaries if number <= e < upper), default=upper)

    def show(n):
        return format(n.normalize(), 'f')

    return f'({show(lower)} – {show(upper)}]'
