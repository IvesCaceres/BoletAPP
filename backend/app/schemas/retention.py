from enum import Enum


class RetentionMode(str, Enum):
    RECEIVER_RETAINS_PPM = "receiver_retains_ppm"
    ISSUER_HANDLES_PPM = "issuer_handles_ppm"
