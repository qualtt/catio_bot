import calendar
from datetime import date
from typing import Tuple

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.content import bot_content

# Mapping of color -> day_number -> custom_emoji_id
EMOJI_IDS = {
    'black': {1: '5413627176471796644', 2: '5413858907137286501', 3: '5411084753401065473', 4: '5413764215993312090', 5: '5413474090952467400', 6: '5411495051626850507', 7: '5411176420888060438', 8: '5411610019311429540', 9: '5413808312422542128', 10: '5411316707404849848', 11: '5411087562309672417', 12: '5413553547847446178', 13: '5413393379927043245', 14: '5413690948146210919', 15: '5411618622130924239', 16: '5413564465654309366', 17: '5411484245489131107', 18: '5411517514305808824', 19: '5413646971976066382', 20: '5413700732081710582', 21: '5413662047311275053', 22: '5413741405422003726', 23: '5413349403756900906', 24: '5413582594711270626', 25: '5413655536140856864', 26: '5413556245086903590', 27: '5413517826604444063', 28: '5413481697339549930', 29: '5411572983808435729', 30: '5413765448648924922', 31: '5413412239128437238'},
    'yellow': {1: '5413479421006881327', 2: '5413493345290854001', 3: '5413544167638868874', 4: '5413397760793685469', 5: '5413881253852128659', 6: '5413666011566088948', 7: '5413455863111262251', 8: '5411214628917127201', 9: '5411143439834205619', 10: '5413603081705268580', 11: '5411178954918766341', 12: '5413472299951106358', 13: '5413671659448085001', 14: '5413428602953835341', 15: '5413692167916919806', 16: '5411317755376867921', 17: '5413716017870316489', 18: '5411509680285460532', 19: '5413807242975683278', 20: '5413457877450925155', 21: '5413357044503717883', 22: '5411255817653497553', 23: '5413501978175120361', 24: '5413470581964187215', 25: '5411442708860415068', 26: '5411591477937609619', 27: '5413718405872138978', 28: '5411476776541004691', 29: '5411091483614827822', 30: '5413606792557012617', 31: '5413472012188296474'},
    'green': {1: '5413432837791589169', 2: '5413856901387560731', 3: '5413841589829151451', 4: '5413661020814090711', 5: '5413665560594523801', 6: '5413799005228409990', 7: '5413497485639328472', 8: '5413356765330843670', 9: '5413384897366629903', 10: '5413414695849730418', 11: '5411339685479883410', 12: '5413443081288601036', 13: '5413809175710966934', 14: '5411559746719227703', 15: '5413671444699720086', 16: '5411431898427729437', 17: '5413867561496390193', 18: '5413559921578910674', 19: '5413845047277824249', 20: '5413660900555008994', 21: '5411183498994164592', 22: '5413499285230624824', 23: '5411099519498627536', 24: '5413568704787031210', 25: '5411397946711254235', 26: '5411546449500480825', 27: '5413480499043669888', 28: '5413848109589506775', 29: '5413642479440273674', 30: '5411544310606769772', 31: '5413362241414145102'},
    'red': {1: '5413330961167328519', 2: '5411534234613492036', 3: '5413337631251535941', 4: '5411446312337975219', 5: '5413737539951436740', 6: '5411367980724429723', 7: '5413744489208520198', 8: '5413520394994888299', 9: '5411272769889410715', 10: '5413705306221882481', 11: '5413882916004474383', 12: '5411091672593374940', 13: '5413327108581663668', 14: '5413480348719817308', 15: '5413581645523495856', 16: '5413556404000692845', 17: '5411104553200296963', 18: '5411609967771822457', 19: '5413537592043937063', 20: '5413337064315859080', 21: '5413717121676908959', 22: '5413796265039275904', 23: '5411538886063073333', 24: '5413455090017150213', 25: '5413722838278382534', 26: '5413420661559306889', 27: '5413455536693748383', 28: '5413871916593228507', 29: '5413488577877157897', 30: '5413625862211807200', 31: '5411415465882852555'}
}


def slot_marker(day: date, min_date: date, max_date: date, free_slots: int, max_slots: int) -> Tuple[str, str]:
    if day < min_date or day > max_date:
        return "⚫️", "black"

    if free_slots <= 0:
        return "⬛️", "black"

    if max_slots <= 1:
        return "🟩", "green"

    ratio = free_slots / max_slots
    if ratio <= 0.34:
        return "🟥", "red"
    if ratio <= 0.6:
        return "🟨", "yellow"
    return "🟩", "green"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month_index = (year * 12 + month - 1) + delta
    return month_index // 12, month_index % 12 + 1


def _month_start(target_date: date) -> date:
    return target_date.replace(day=1)


def build_month_calendar(
    *,
    year: int,
    month: int,
    availability: dict[date, int],
    min_date: date,
    max_date: date,
    max_slots: int,
    footer_buttons: list[tuple[str, str]] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    calendar.setfirstweekday(calendar.MONDAY)

    current_month = date(year, month, 1)
    month_weeks = calendar.monthcalendar(year, month)
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)
    prev_enabled = date(prev_year, prev_month, 1) >= _month_start(min_date)
    next_enabled = date(next_year, next_month, 1) <= _month_start(max_date)

    builder.button(
        text="‹",
        callback_data=f"cal_nav_{prev_year}_{prev_month}" if prev_enabled else "noop",
    )
    builder.button(text=f"{bot_content.month_name(month)} {year}", callback_data="noop")
    builder.button(
        text="›",
        callback_data=f"cal_nav_{next_year}_{next_month}" if next_enabled else "noop",
    )

    for weekday in bot_content.weekday_names():
        builder.button(text=weekday, callback_data="noop")

    for week in month_weeks:
        for day_number in week:
            if day_number == 0:
                builder.button(text=" ", callback_data="noop")
                continue

            day = current_month.replace(day=day_number)
            free_slots = availability.get(day, 0)
            marker, color_name = slot_marker(day, min_date, max_date, free_slots, max_slots)
            enabled = min_date <= day <= max_date and free_slots > 0
            callback_data = f"cal_day_{day.isoformat()}" if enabled else "noop"
            
            custom_emoji_id = EMOJI_IDS.get(color_name, {}).get(day_number)
            if custom_emoji_id:
                builder.button(
                    text=" ", 
                    callback_data=callback_data,
                    icon_custom_emoji_id=custom_emoji_id
                )
            else:
                builder.button(text=f"{marker} {day_number}", callback_data=callback_data)

    footer_buttons = footer_buttons or []
    for text, callback_data in footer_buttons:
        builder.button(text=text, callback_data=callback_data)

    builder.adjust(3, 7, *([7] * len(month_weeks)), *([1] * len(footer_buttons)))
    return builder.as_markup()
