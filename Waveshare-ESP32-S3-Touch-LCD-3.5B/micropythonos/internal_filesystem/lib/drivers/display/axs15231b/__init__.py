import sys

from . import axs15231b
from . import _axs15231b_init

# Register _axs15231b_init in sys.modules so __import__('_axs15231b_init')
# can find it.  This is needed because display_driver_framework.py uses
# __import__('_axs15231b_init') expecting a top-level module, while
# _axs15231b_init lives in the axs15231b package subdirectory.
sys.modules['_axs15231b_init'] = _axs15231b_init

# Explicitly define __all__ and re-export public symbols from the axs15231b
# module
__all__ = [
    'AXS15231B',
    'STATE_HIGH',
    'STATE_LOW',
    'STATE_PWM',
    'BYTE_ORDER_RGB',
    'BYTE_ORDER_BGR',
]

# Re-export the public symbols
AXS15231B = axs15231b.AXS15231B
STATE_HIGH = axs15231b.STATE_HIGH
STATE_LOW = axs15231b.STATE_LOW
STATE_PWM = axs15231b.STATE_PWM
BYTE_ORDER_RGB = axs15231b.BYTE_ORDER_RGB
BYTE_ORDER_BGR = axs15231b.BYTE_ORDER_BGR
