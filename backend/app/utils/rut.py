"""
Utilidades para validación y formateo de RUT chileno.
"""
import re
import logging

logger = logging.getLogger(__name__)

# RUT syntax validation; no individual identifiers are included here.
RUT_PATTERN = re.compile(r"^(\d{1,2}\.?\d{3}\.?\d{3})-?(\d|k|K)$")


def clean_rut(rut: str) -> str:
    """Normalize a supplied RUT; test-only examples are defined in the fictional fixtures."""
    if not rut:
        return ""
    cleaned = rut.replace(".", "").replace("-", "").upper().strip()
    return cleaned


def validate_rut(rut: str) -> bool:
    """
    Valida si un RUT chileno es válido usando algoritmo módulo 11.

    Args:
        rut: RUT en formato string (puede tener puntos y guión)

    Returns:
        True si el RUT es válido
    """
    if not rut:
        return False

    cleaned = clean_rut(rut)

    match = RUT_PATTERN.match(cleaned)
    if not match:
        return False

    number_part = match.group(1)
    verifier = match.group(2)

    try:
        number = int(number_part)
    except ValueError:
        return False

    # Algoritmo módulo 11
    factors = [2, 3, 4, 5, 6, 7]
    total = 0
    reverse_digits = reversed(str(number))

    for i, digit in enumerate(reverse_digits):
        total += int(digit) * factors[i % 6]

    remainder = total % 11
    expected_check = 11 - remainder

    if expected_check == 11:
        expected_verifier = "0"
    elif expected_check == 10:
        expected_verifier = "K"
    else:
        expected_verifier = str(expected_check)

    is_valid = verifier.upper() == expected_verifier

    if not is_valid:
        logger.debug(f"RUT {rut} inválido: esperado {expected_verifier}, got {verifier}")

    return is_valid


def format_rut(rut: str) -> str:
    """
    Formatea un RUT al formato estándar XX.XXX.XXX-X.

    Args:
        rut: RUT potencialmente sucio

    Returns:
        RUT formateado o string vacío si es inválido
    """
    if not rut:
        return ""

    cleaned = clean_rut(rut)

    match = RUT_PATTERN.match(cleaned)
    if not match:
        # Intentar con formato sin guión
        if len(cleaned) < 8 or len(cleaned) > 9:
            return ""
        number = cleaned[:-1]
        verifier = cleaned[-1]
    else:
        number = match.group(1)
        verifier = match.group(2)

    # Formatear número con puntos
    formatted_number = ""
    for i, digit in enumerate(reversed(number)):
        if i > 0 and i % 3 == 0:
            formatted_number = "." + formatted_number
        formatted_number = digit + formatted_number

    return f"{formatted_number}-{verifier.upper()}"


def split_rut(rut: str) -> tuple[str, str] | None:
    """
    Separa un RUT en número y dígito verificador.

    Returns:
        Tupla (numero, dv) o None si es inválido
    """
    if not validate_rut(rut):
        return None

    cleaned = clean_rut(rut)
    return cleaned[:-1], cleaned[-1]