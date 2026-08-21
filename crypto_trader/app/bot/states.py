from aiogram.fsm.state import State, StatesGroup


class CreateTaskStates(StatesGroup):
    trading_channel = State()
    symbol = State()
    ema_params = State()
    levels = State()
    delta_ticks = State()
    tp_ticks = State()
    sl_ticks = State()
    trading_hours = State()
    position_lots = State()


class CreateAdvisorTaskStates(StatesGroup):
    symbol = State()
    market = State()
    ema_params = State()
    trading_hours = State()
    alias = State()


class EditAdvisorTaskStates(StatesGroup):
    symbol = State()
    market = State()
    ema_params = State()
    trading_hours = State()
    alias = State()


class FundingScanStates(StatesGroup):
    top_n = State()
    threshold = State()


class SlFollowStates(StatesGroup):
    confirm_enable = State()
    confirm_disable = State()


class CreateAtrPullbackStates(StatesGroup):
    symbol = State()
    ema_params = State()
    btf_interval = State()
    mtf_interval = State()
    trading_hours = State()
    alias = State()
    auto_trade = State()
    position_usd = State()
    leverage = State()
    confirm = State()


class EditScalpStrategyStates(StatesGroup):
    value = State()


class EditScalpLevelsStates(StatesGroup):
    add = State()


class PumpScanStates(StatesGroup):
    value = State()
    scan_at = State()


class PumpOpenPositionStates(StatesGroup):
    ema = State()
    position_usd = State()
    leverage = State()
    confirm = State()


class CreateScalpAdvisorStates(StatesGroup):
    symbol = State()
    levels = State()
    trading_hours = State()
    alias = State()
    confirm = State()
