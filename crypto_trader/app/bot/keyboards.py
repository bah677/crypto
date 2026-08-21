from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_kb(*, advisor_mode: bool = False) -> InlineKeyboardMarkup:
    if advisor_mode:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Новое задание", callback_data="adv:new"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📋 Список заданий", callback_data="adv:list"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📊 Статус", callback_data="advisor:status"
                    ),
                    InlineKeyboardButton(
                        text="🔔 Алерты", callback_data="alerts:menu"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="❓ Справка (/help)", callback_data="advisor:help"
                    )
                ],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Новое торговое задание", callback_data="task:new"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Список заданий", callback_data="task:list"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❓ Справка (/help)", callback_data="advisor:help"
                )
            ],
        ]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="task:cancel")],
        ]
    )


def levels_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Уровни внесены", callback_data="task:levels_done"
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="task:cancel")],
        ],
    )


def task_toggle_kb(task_id: int, enabled: bool) -> InlineKeyboardMarkup:
    label = "⏸ Выключить" if enabled else "▶️ Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"task:toggle:{task_id}"
                )
            ],
            [InlineKeyboardButton(text="« Меню", callback_data="task:menu")],
        ]
    )


def advisor_task_manage_kb(task_id: int, enabled: bool) -> InlineKeyboardMarkup:
    toggle = "⏸ Выключить" if enabled else "▶️ Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle, callback_data=f"adv:toggle:{task_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать", callback_data=f"adv:edit:{task_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"adv:delete:{task_id}"
                )
            ],
            [
                InlineKeyboardButton(text="« К списку", callback_data="adv:list"),
                InlineKeyboardButton(text="« Меню", callback_data="task:menu"),
            ],
        ]
    )


def advisor_delete_confirm_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить", callback_data=f"adv:delete_ok:{task_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="« Отмена", callback_data=f"adv:view:{task_id}"
                )
            ],
        ]
    )


def sl_follow_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, включить автоследование SL",
                    callback_data="sf:confirm:yes",
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="sf:cancel")],
        ]
    )


def sl_follow_disable_confirm_kb(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, выключить",
                    callback_data=f"sf:off_ok:{symbol}",
                )
            ],
            [InlineKeyboardButton(text="« Отмена", callback_data="sf:cancel")],
        ]
    )


def back_menu_kb(*, advisor_mode: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Меню", callback_data="task:menu")],
        ]
    )
