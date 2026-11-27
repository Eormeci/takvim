"""Renk paletlerini yan yana gösterir:  python -m takvim.temalar"""
from datetime import date

from rich.console import Console, Group
from rich.text import Text
from rich.theme import Theme

from .api import PALETLER, fmt_day, month_text


def preview(name: str) -> Group:
    today = date.today()
    busy = {today, today.replace(day=min(today.day + 3, 28)), today.replace(day=max(today.day - 5, 1))}
    cal = month_text(today.year, today.month, busy, today)
    t = Text()
    t.append(fmt_day(today), style="baslik")
    t.append("  · bugün", style="bugun")
    t.append("\n› ", style="vurgu")
    t.append("09:00–10:30  Toplantı", style="secili")
    t.append("\n  ")
    t.append("18:00        ", style="plan")
    t.append("Spor")
    t.append("  (salon)", style="soluk")
    t.append("\n\n")
    t.append("Başlık: ", style="vurgu")
    t.append("Doktor\n")
    t.append("✓", style="tamam")
    t.append(" Kaydedildi  ")
    t.append("Hata örneği", style="hata")
    t.append("\n←↑↓→ gez · a ekle · q kapat", style="soluk")
    return Group(Text(f"{list(PALETLER).index(name) + 1}) {name.upper()}\n", style="baslik"), cal, Text(), t)


def main():
    for name, colors in PALETLER.items():
        console = Console(theme=Theme(colors), highlight=False)
        console.print(preview(name))
        console.print()


if __name__ == "__main__":
    main()
