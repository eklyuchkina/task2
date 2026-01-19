import asyncio
import logging
import os

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
OWM_KEY = os.getenv("OWM_API_KEY")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN not found")

logging.basicConfig(level=logging.INFO)

bot = Bot(TOKEN)
dp = Dispatcher()

users = {}
food_wait = {}


class Profile(StatesGroup):
    weight = State()
    height = State()
    age = State()
    activity = State()
    city = State()
    goal_choice = State()
    goal_manual = State()


class Food(StatesGroup):
    grams = State()


def get_user(uid: int):
    if uid not in users:
        users[uid] = {
            "weight": 0,
            "height": 0,
            "age": 0,
            "activity": 0,
            "city": "",
            "water_goal": 0,
            "cal_goal": 0,
            "water": 0,
            "food": 0.0,
            "burned": 0.0,
            "extra_water": 0,
        }
    return users[uid]


def ready(u: dict):
    return u["weight"] > 0 and u["height"] > 0 and u["age"] > 0 and u["city"]


async def get_temp(city: str):
    if not OWM_KEY:
        return None
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": OWM_KEY, "units": "metric"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return float(data["main"]["temp"])
    except Exception:
        return None


def calc_water(u: dict, temp):
    base = u["weight"] * 30
    act = (u["activity"] // 30) * 500

    heat = 0
    if temp is not None and temp > 25:
        heat = 500 if temp <= 30 else 1000

    return int(base + act + heat + u["extra_water"])


def calc_cal(u: dict):
    base = 10 * u["weight"] + 6.25 * u["height"] - 5 * u["age"]

    a = u["activity"]
    if a <= 30:
        bonus = 200
    elif a <= 60:
        bonus = 300
    else:
        bonus = 400

    return int(base + bonus)


async def off_search(name: str):
    url = "https://world.openfoodfacts.org/cgi/search.pl"
    params = {"action": "process", "search_terms": name, "json": "true", "page_size": 5}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=12) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                prods = data.get("products", [])
                if not prods:
                    return None

                for p in prods:
                    n = (p.get("product_name") or "").strip()
                    kcal = (p.get("nutriments", {}) or {}).get("energy-kcal_100g")
                    if n and kcal is not None:
                        try:
                            return n, float(kcal)
                        except Exception:
                            pass

                first = prods[0]
                return (first.get("product_name") or name), 0.0
    except Exception:
        return None


def workout_kcal(t: str, minutes: int):
    t = t.lower().strip()
    rates = {
        "бег": 10, "run": 10,
        "ходьба": 4, "walk": 4,
        "велосипед": 7, "bike": 7,
        "плавание": 8, "swim": 8,
        "силовая": 6, "gym": 6,
        "йога": 3, "yoga": 3,
    }
    rate = rates.get(t, 6)
    return rate * minutes


@dp.message(Command("start"))
async def start(m: Message):
    logging.info("cmd from %s: %s", m.from_user.id, m.text)
    await m.answer(
        "Привет! Команды:\n"
        "/set_profile\n"
        "/log_water 300\n"
        "/log_food банан\n"
        "/log_workout бег 30\n"
        "/check_progress\n"
        "/reset_day"
    )


@dp.message(Command("set_profile"))
async def set_profile(m: Message, state: FSMContext):
    logging.info("cmd from %s: %s", m.from_user.id, m.text)
    get_user(m.from_user.id)
    await state.set_state(Profile.weight)
    await m.answer("Вес (кг)?")


@dp.message(Profile.weight)
async def p_weight(m: Message, state: FSMContext):
    try:
        w = float(m.text.replace(",", "."))
        if w <= 0:
            raise ValueError
    except Exception:
        return await m.answer("Напиши вес числом, например 80")
    u = get_user(m.from_user.id)
    u["weight"] = w
    await state.set_state(Profile.height)
    await m.answer("Рост (см)?")


@dp.message(Profile.height)
async def p_height(m: Message, state: FSMContext):
    try:
        h = float(m.text.replace(",", "."))
        if h <= 0:
            raise ValueError
    except Exception:
        return await m.answer("Напиши рост числом, например 184")
    u = get_user(m.from_user.id)
    u["height"] = h
    await state.set_state(Profile.age)
    await m.answer("Возраст?")


@dp.message(Profile.age)
async def p_age(m: Message, state: FSMContext):
    try:
        a = int(m.text)
        if a <= 0:
            raise ValueError
    except Exception:
        return await m.answer("Напиши возраст числом, например 26")
    u = get_user(m.from_user.id)
    u["age"] = a
    await state.set_state(Profile.activity)
    await m.answer("Минут активности в день? (например 45)")


@dp.message(Profile.activity)
async def p_activity(m: Message, state: FSMContext):
    try:
        a = int(m.text)
        if a < 0:
            raise ValueError
    except Exception:
        return await m.answer("Напиши число минут, например 45")
    u = get_user(m.from_user.id)
    u["activity"] = a
    await state.set_state(Profile.city)
    await m.answer("Город? (например Moscow)")


@dp.message(Profile.city)
async def p_city(m: Message, state: FSMContext):
    city = (m.text or "").strip()
    if not city:
        return await m.answer("Напиши город текстом, например Paris")
    u = get_user(m.from_user.id)
    u["city"] = city
    await state.set_state(Profile.goal_choice)
    await m.answer("Цель калорий: 1 — авто, 2 — вручную")


@dp.message(Profile.goal_choice)
async def p_goal_choice(m: Message, state: FSMContext):
    if m.text not in ("1", "2"):
        return await m.answer("Напиши 1 или 2")
    if m.text == "2":
        await state.set_state(Profile.goal_manual)
        return await m.answer("Введи цель калорий (например 2500)")

    u = get_user(m.from_user.id)
    temp = await get_temp(u["city"])
    u["cal_goal"] = calc_cal(u)
    u["water_goal"] = calc_water(u, temp)
    await state.clear()

    t = "нет данных" if temp is None else f"{temp:.1f}°C"
    await m.answer(f"Сохранил Температура: {t}\nВода: {u['water_goal']} мл\nКалории: {u['cal_goal']} ккал")


@dp.message(Profile.goal_manual)
async def p_goal_manual(m: Message, state: FSMContext):
    try:
        g = int(m.text)
        if g <= 0:
            raise ValueError
    except Exception:
        return await m.answer("Напиши число, например 2500")

    u = get_user(m.from_user.id)
    temp = await get_temp(u["city"])
    u["cal_goal"] = g
    u["water_goal"] = calc_water(u, temp)
    await state.clear()

    t = "нет данных" if temp is None else f"{temp:.1f}°C"
    await m.answer(f"Сохранил Температура: {t}\nВода: {u['water_goal']} мл\nКалории: {u['cal_goal']} ккал")


@dp.message(Command("log_water"))
async def log_water(m: Message):
    logging.info("cmd from %s: %s", m.from_user.id, m.text)
    u = get_user(m.from_user.id)
    if not ready(u) or u["water_goal"] <= 0:
        return await m.answer("Сначала /set_profile")

    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return await m.answer("Пример: /log_water 300")

    try:
        ml = int(parts[1].strip())
        if ml <= 0:
            raise ValueError
    except Exception:
        return await m.answer("Напиши мл числом, например 300")

    u["water"] += ml
    left = max(u["water_goal"] - u["water"], 0)
    await m.answer(f"💧 +{ml} мл. Всего {u['water']}/{u['water_goal']} мл. Осталось {left} мл.")


@dp.message(Command("log_food"))
async def log_food(m: Message, state: FSMContext):
    logging.info("cmd from %s: %s", m.from_user.id, m.text)
    u = get_user(m.from_user.id)
    if not ready(u) or u["cal_goal"] <= 0:
        return await m.answer("Сначала /set_profile")

    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        return await m.answer("Пример: /log_food банан")

    query = parts[1].strip()
    res = await off_search(query)
    if not res:
        return await m.answer("Не нашёл продукт, попробуй другое название (можно на англ.)")

    name, kcal100 = res
    food_wait[m.from_user.id] = {"name": name, "kcal100": kcal100}
    await state.set_state(Food.grams)
    await m.answer(f"{name} — {kcal100:.1f} ккал/100г. Сколько грамм?")


@dp.message(Food.grams)
async def food_grams(m: Message, state: FSMContext):
    uid = m.from_user.id
    u = get_user(uid)
    if uid not in food_wait:
        await state.clear()
        return await m.answer("Повтори /log_food <продукт>")

    try:
        g = float(m.text.replace(",", "."))
        if g <= 0:
            raise ValueError
    except Exception:
        return await m.answer("Напиши граммы числом, например 150")

    name = food_wait[uid]["name"]
    kcal100 = food_wait[uid]["kcal100"]
    kcal = kcal100 * g / 100.0

    u["food"] += kcal
    del food_wait[uid]
    await state.clear()

    await m.answer(f"Записал: {name}, {g:.0f} г = {kcal:.1f} ккал")


@dp.message(Command("log_workout"))
async def log_workout(m: Message):
    logging.info("cmd from %s: %s", m.from_user.id, m.text)
    u = get_user(m.from_user.id)
    if not ready(u):
        return await m.answer("Сначала /set_profile")

    parts = m.text.split()
    if len(parts) < 3:
        return await m.answer("Пример: /log_workout бег 30")

    t = " ".join(parts[1:-1])
    try:
        mins = int(parts[-1])
        if mins <= 0:
            raise ValueError
    except Exception:
        return await m.answer("Минуты должны быть числом, например 30")

    burned = workout_kcal(t, mins)
    u["burned"] += burned

    extra = (mins // 30) * 200
    u["extra_water"] += extra

    temp = await get_temp(u["city"])
    u["water_goal"] = calc_water(u, temp)

    if extra > 0:
        await m.answer(f"🏃 {t} {mins} мин — {burned:.0f} ккал. Плюс выпей {extra} мл воды.")
    else:
        await m.answer(f"🏃 {t} {mins} мин — {burned:.0f} ккал.")


@dp.message(Command("check_progress"))
async def check(m: Message):
    logging.info("cmd from %s: %s", m.from_user.id, m.text)
    u = get_user(m.from_user.id)
    if not ready(u):
        return await m.answer("Сначала /set_profile")

    water_left = max(u["water_goal"] - u["water"], 0)

    balance = u["food"] - u["burned"]
    cal_left = max(u["cal_goal"] - balance, 0)

    await m.answer(
        "📊 Прогресс:\n\n"
        f"Вода: {u['water']}/{u['water_goal']} мл (осталось {water_left} мл)\n\n"
        f"Калории:\n"
        f"- съедено: {u['food']:.1f} ккал из {u['cal_goal']} ккал\n"
        f"- сожжено: {u['burned']:.1f} ккал\n"
        f"- баланс: {balance:.1f} ккал\n"
        f"- осталось до цели: {cal_left:.0f} ккал"
    )


@dp.message(Command("reset_day"))
async def reset_day(m: Message):
    logging.info("cmd from %s: %s", m.from_user.id, m.text)
    u = get_user(m.from_user.id)
    u["water"] = 0
    u["food"] = 0.0
    u["burned"] = 0.0
    u["extra_water"] = 0
    await m.answer("Сбросил записи за день")


async def health(request):
    return web.Response(text="ok")


async def run_web():
    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    await run_web()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
