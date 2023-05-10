from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
import schedule
import os
import psycopg2
import asyncio
import datetime
import logging
from aiogram.contrib.fsm_storage.memory import MemoryStorage


bot = Bot(token=os.environ['BOT_TOKEN'])
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
logger = logging.getLogger(__name__)

conn = psycopg2.connect(
        host=os.environ['POSTGRES_HOST'],
        port=os.environ['POSTGRES_PORT'],
        database=os.environ['POSTGRES_DB'],
        user=os.environ['POSTGRES_USER'],
        password=os.environ['POSTGRES_PASSWORD']
    )
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS Users (
    id bigint PRIMARY KEY,
    username text
);
    ''')
conn.commit()


async def delete_old_records(table: str):
    # Вычисляем дату, месяц назад от текущей даты
    one_month_ago = datetime.datetime.now() - datetime.timedelta(days=30)

    # Формируем SQL-запрос для удаления записей
    cursor.execute(f"DELETE FROM {table} WHERE created_date < '{one_month_ago}'")
    conn.commit()


async def create_DB(user):
    cursor.execute(f'''
CREATE TABLE IF NOT EXISTS {user} (
    id bigint generated always as identity PRIMARY KEY,
    service text,
    login text,
    password text,
    user_id bigint references Users,
    Created_date date
);
    ''')
    conn.commit()

async def select_DB(table, user_id):
    cursor.execute(f'SELECT id, service, login, password FROM {table} WHERE user_id={user_id}')
    return cursor.fetchall()


async def select_service(table, user_id, id):
    cursor.execute(f'SELECT login, password, service FROM {table} WHERE user_id = {user_id} AND id = {id}')
    return cursor.fetchall()


async def insert_service(table, user_id, service, login, password):
    create_date = datetime.datetime.now()
    cursor.execute(f'''
    INSERT INTO {table} (service, login, password, user_id, created_date) 
    VALUES ('{service}', '{login}', '{password}', {user_id}, '{create_date}')
''')
    conn.commit()

async def drop_service(table, id):
    cursor.execute(f"DELETE FROM {table} WHERE id = '{id}'")
    conn.commit()


class User(StatesGroup):
    waiting_for_service = State()
    waiting_for_login = State()
    waiting_for_password = State()
    waiting_for_command = State()
    waiting_for_get_service = State()
    waiting_for_del_service = State()

# start command
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    user_id, user_name = message.from_user.id, message.from_user.username
    await message.answer('''Привет! Я бот-хранитель паролей. Вы можете использовать меня, 
                         чтобы хранить пароли для различных сервисов. Для начала напишите /help, чтобы узнать больше.''')
    await create_DB(f"{user_name}_{user_id}")
    cursor.execute("INSERT INTO Users (id, username) VALUES(%s, %s) "
                   "ON CONFLICT (id) DO UPDATE SET id=%s, username=%s", (user_id, user_name, user_id, user_name))
    conn.commit()
    await User.waiting_for_command.set()

@dp.message_handler(commands=['help'], state=User.waiting_for_command)
async def help_message(message: types.Message):
    await message.answer('Я могу делать следующее:\n\n/set - добавляет логин и пароль к сервису\n/get - получает логин и пароль по названию сервиса\n/del - удаляет значения для сервиса')
    await User.waiting_for_command.set()

@dp.message_handler(commands=['get'], state=User.waiting_for_command)
async def get_request_password(message: types.Message, state: FSMContext):
    user_name, user_id = message.from_user.username, message.from_user.id
    table = user_name.lower() + "_" + str(user_id)
    try:
        data = await select_DB(table=table, user_id=user_id)
        if data is not None and len(data) != 0:
            result = "Доступные сервисы"
            for i in range(len(data)):
                id = data[i][0]
                item = data[i][1]
                result += f"\n/{id} - {item}"
            await message.answer(result)
            await message.answer("нажмите на нужный номер")
            await User.waiting_for_get_service.set()
        else:
            await message.answer("У вас нет сохраненных паролей. Нажмите /set, чтобы записать сервис.")
            await User.waiting_for_command.set()
    except Exception as e:
        logging.error(f"Ошибка при выполнении функции get_request_password: {e}")
        await message.answer("Произошла ошибка при выполнении команды /get.\nНажмите /help, чтобы узнать способности этого бота ")
        await User.waiting_for_command.set()


@dp.message_handler(state=User.waiting_for_get_service)
async def get_password(message: types.Message, state: FSMContext):
    user_name, user_id = message.from_user.username, message.from_user.id
    id =  message.text[1:]
    table = user_name.lower() + "_" + str(user_id)
    try:
        id = int(id)
    except Exception as e:
        logger.exception(f"Ошибка от {user_name} - {user_id}")
        await message.answer("Произошла ошибка при выполнении команды.\nНажмите /help, чтобы узнать способности этого бота")
    else:
        data = await select_service(table, user_id, id)
        if data is not None and len(data) != 0:
            login, password, service = data[0][0], data[0][1], data[0][2]
            message_id = (await message.answer('Ваш логин и пароль для сервиса {}: \nЛогин: {}\nПароль: {}'.format(
                    service, login, password))).message_id
            message_id_2 = (await message.answer("Сообщение исчезнит через 10 секунд ...")).message_id
            await User.waiting_for_command.set()
            await asyncio.sleep(10)
            await bot.delete_message(chat_id=user_id, message_id=message_id)
            await bot.delete_message(chat_id=user_id, message_id=message_id_2)
        else:
            await message.answer("Неверно набран сервис. Нажмите /get чтобы запросить доступные сервисы")
    finally:
        await User.waiting_for_command.set()

# set command
@dp.message_handler(commands=['set'], state=User.waiting_for_command)
async def set_password_command(message: types.Message, state: FSMContext):
    await message.answer('Введите сервис')        
    await User.waiting_for_service.set()
    
@dp.message_handler(state=User.waiting_for_service, content_types=types.ContentType.TEXT)
async def set_password_service(message: types.Message, state: FSMContext):
    user_service = message.text
    if "/" == user_service[0]:
        await message.answer("Вы вышли из заполнения сервиса")
        await User.waiting_for_command.set()
    else:
        message_login_bot = (await message.answer('Введите логин')).message_id
        await state.update_data(service=user_service)
        await state.update_data(login_bot=message_login_bot)
        await User.waiting_for_login.set()

@dp.message_handler(state=User.waiting_for_login, content_types=types.ContentType.TEXT) 
async def set_password_login(message: types.Message, state: FSMContext):
    user_login = message.text
    if "/" == user_login[0]:
        await message.answer("Вы вышли из заполнения логина")
        await User.waiting_for_command.set()
    else:
        message_login = message.message_id
        message_pass_bot = (await message.answer('Введите пароль')).message_id
        async with state.proxy() as data:
            data['login'] = user_login
            data['login_id'] = message_login
            data['pass_bot'] = message_pass_bot
        await User.waiting_for_password.set()


@dp.message_handler(state=User.waiting_for_password, content_types=types.ContentType.TEXT)
async def set_password_password(message: types.Message, state: FSMContext):
    user_id, user_name = message.chat.id, message.from_user.username
    user_password = message.text
    if "/" == user_password[0]:
        await message.answer("Вы вышли из заполнения пароля")
        await User.waiting_for_command.set()
    else:
        message_pass = message.message_id
        async with state.proxy() as data:
            user_service = data['service']
            user_login = data['login']
            message_login = data['login_id']
            message_login_bot = data['login_bot']
            message_pass_bot = data['pass_bot']
        await User.waiting_for_command.set()
        await insert_service(f"{user_name}_{user_id}", user_id, user_service, user_login, user_password)
        await bot.delete_message(chat_id=user_id, message_id=message_login)
        await bot.delete_message(chat_id=user_id, message_id=message_pass)
        await bot.delete_message(chat_id=user_id, message_id=message_login_bot)
        await bot.delete_message(chat_id=user_id, message_id=message_pass_bot)
        await message.answer('Логин и пароль для сервиса {} успешно сохранены!'.format(user_service))



# del command
@dp.message_handler(commands=['del'], state=User.waiting_for_command)
async def del_request_password(message: types.Message, state: FSMContext):
    user_name, user_id = message.from_user.username, message.from_user.id
    table = user_name.lower() + "_" + str(user_id)
    try:
        data = await select_DB(table=table, user_id=user_id)
        if data is not None and len(data) != 0:
            result = "Доступные сервисы"
            for i in range(len(data)):
                id = data[i][0]
                item = data[i][1]
                result += f"\n/{id} - {item}"
            await message.answer(result)
            await message.answer("Нажмите на номер сервиса, который хотите удалить")
            await User.waiting_for_del_service.set()
        else:
            await message.answer("У вас нет сохраненных паролей. Нажмите /set, чтобы записать сервис.")
            await User.waiting_for_command.set()
    except Exception as e:
        logging.error(f"Ошибка при выполнении функции del_request_password: {e}")
        await message.answer("Произошла ошибка при выполнении команды /del.\nНажмите /help, чтобы узнать способности этого бота")


@dp.message_handler(state=User.waiting_for_del_service)
async def del_password(message: types.Message, state: FSMContext):
    user_name, user_id = message.from_user.username, message.from_user.id
    id = message.text[1:]
    try:
        id = int(id)
    except Exception as e:
        logger.exception(f"Ошибка от {user_name} - {user_id}")
        await message.answer("Произошла ошибка при выполнении команды. Попробуйте снова позже.")
    else:
        data = await select_service(f"{user_name}_{user_id}", user_id, id)
        if data is not None and len(data) != 0:
            await drop_service(f"{user_name}_{user_id}", id)
            await message.answer(f'Сервис {data[0][2]} успешно удален')
            await User.waiting_for_command.set()
        else:
            await message.answer("Неверно набран сервис. Нажмите /del чтобы запросить снова")
            await User.waiting_for_command.set()
    finally:
        await User.waiting_for_command.set()

if __name__ == '__main__':
    # set up state machine for setting passwords
    try:
        executor.start_polling(dp, skip_updates=True)
        schedule.every().monday.at("00:30").do(delete_old_records)
    finally:
        cursor.close()
        conn.close()

    

