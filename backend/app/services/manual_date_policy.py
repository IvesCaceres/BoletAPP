"""Local calendar bounds for preparing manual BHE items."""

from datetime import date, timedelta


MAX_ISSUE_AGE_DAYS = 90


def local_today() -> date:
    return date.today()


def allowed_issue_dates(today: date | None = None) -> tuple[date, date]:
    current = today if today is not None else local_today()
    return current - timedelta(days=MAX_ISSUE_AGE_DAYS), current


def issue_date_error(value: date, today: date | None = None) -> str | None:
    minimum, maximum = allowed_issue_dates(today)
    if value < minimum:
        return "La fecha de emisión no puede tener más de 90 días de antigüedad."
    if value > maximum:
        return "La fecha de emisión no puede ser futura."
    return None
