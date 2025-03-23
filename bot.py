import time
import sqlite3
import logging
import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
import base64
from cryptography.fernet import Fernet

# Tải biến môi trường từ file .env
load_dotenv()

# Cấu hình logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Biến toàn cục
click_count = 0
income_today = 0
last_click_time = 0
last_update_time = 0
target_clicks = 1000
admin_user_id = int(os.getenv("ADMIN_USER_ID"))  # ID của admin
bank_account_info = {
    'account_number': '',
    'bank_name': '',
    'phone_number': '',
    'deposited_amount': 0,
    'withdrawn_amount': 0,
}

# Khóa mã hóa
encryption_key = base64.urlsafe_b64encode(os.urandom(32))
cipher_suite = Fernet(encryption_key)

# Kết nối cơ sở dữ liệu
def connect_db():
    try:
        return sqlite3.connect('user_data.db')
    except sqlite3.Error as e:
        logging.error(f"Lỗi kết nối cơ sở dữ liệu: {e}")
        return None

def init_db():
    with connect_db() as conn:
        if conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    account_number TEXT,
                    bank_name TEXT,
                    phone_number TEXT,
                    deposited_amount REAL,
                    withdrawn_amount REAL,
                    income_today REAL,
                    click_count INTEGER
                )
            ''')
            conn.commit()

def encrypt_data(data: str) -> str:
    return cipher_suite.encrypt(data.encode()).decode()

def decrypt_data(data: str) -> str:
    return cipher_suite.decrypt(data.encode()).decode()

def read_user_data(user_id: int):
    with connect_db() as conn:
        if conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            user_data = cursor.fetchone()
            if user_data:
                return {
                    'User ID': user_data[0],
                    'Account Number': decrypt_data(user_data[1]) if user_data[1] else '',
                    'Bank Name': decrypt_data(user_data[2]) if user_data[2] else '',
                    'Phone Number': decrypt_data(user_data[3]) if user_data[3] else '',
                    'Deposited Amount': user_data[4],
                    'Withdrawn Amount': user_data[5],
                    'Income Today': user_data[6],
                    'Click Count': user_data[7]
                }
    return None

def save_user_data(user_id: int):
    with connect_db() as conn:
        if conn:
            cursor = conn.cursor()
            user_data = (
                user_id,
                encrypt_data(bank_account_info['account_number']),
                encrypt_data(bank_account_info['bank_name']),
                encrypt_data(bank_account_info['phone_number']),
                bank_account_info['deposited_amount'],
                bank_account_info['withdrawn_amount'],
                income_today,
                click_count
            )
            try:
                cursor.execute('BEGIN TRANSACTION;')
                cursor.execute('''
                    INSERT INTO users (user_id, account_number, bank_name, phone_number, 
                                       deposited_amount, withdrawn_amount, income_today, click_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        account_number = excluded.account_number,
                        bank_name = excluded.bank_name,
                        phone_number = excluded.phone_number,
                        deposited_amount = excluded.deposited_amount,
                        withdrawn_amount = excluded.withdrawn_amount,
                        income_today = excluded.income_today,
                        click_count = excluded.click_count
                ''', user_data)
                cursor.execute('COMMIT;')
            except Exception as e:
                cursor.execute('ROLLBACK;')
                logging.error(f"Lỗi khi lưu dữ liệu: {e}")

async def send_user_notification(user_chat_id: int, message: str):
    try:
        await application.bot.send_message(chat_id=user_chat_id, text=message)
    except Exception as e:
        logging.error(f"Lỗi khi gửi tin nhắn: {e}")
        await application.bot.send_message(chat_id=user_chat_id, text="❌ Đã xảy ra lỗi khi xử lý yêu cầu của bạn. Vui lòng thử lại.")

async def notify_admin(user_id: int, user_info: str, action: str, amount: float, user_chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    message = (
        f"📥 Người dùng (ID: {user_id}) đã {action} tiền: {amount} VND\n"
        f"Thông tin: {user_info}\n"
        f"Bạn có muốn duyệt không?"
    )
    context.user_data['pending_transaction'] = {'action': action, 'amount': amount, 'user_chat_id': user_chat_id}
    
    keyboard = [
        [InlineKeyboardButton("Duyệt", callback_data='approve_transaction')],
        [InlineKeyboardButton("Không Duyệt", callback_data='deny_transaction')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await send_user_notification(user_chat_id, "🔄 Đang xử lý giao dịch...")
    await context.bot.send_message(chat_id=admin_user_id, text=message, reply_markup=reply_markup)

async def approve_transaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    transaction = context.user_data.get('pending_transaction')
    if not transaction:
        logging.warning("Không có giao dịch nào để phê duyệt.")
        return

    user_chat_id = transaction['user_chat_id']
    action = transaction['action']
    amount = transaction['amount']
    
    global bank_account_info
    message = ""

    logging.info(f"Phê duyệt giao dịch: {action} {amount} VND từ người dùng {user_chat_id}")

    if action == "nạp":
        bank_account_info['deposited_amount'] += amount
        message = f"✅ Giao dịch nạp tiền {amount} VND thành công!"
    elif action == "rút":
        total_balance = bank_account_info['deposited_amount'] + (click_count * (bank_account_info['deposited_amount'] * 0.00005))
        if amount <= total_balance:
            bank_account_info['withdrawn_amount'] += amount
            bank_account_info['deposited_amount'] -= amount
            message = f"✅ Giao dịch rút tiền {amount} VND thành công!"
        else:
            message = f"❌ Giao dịch rút tiền {amount} VND thất bại do số dư không đủ!"
    elif action == "chuyển khoản":
        recipient_user_id = context.user_data.get('recipient_user_id')
        if recipient_user_id:
            recipient_data = read_user_data(recipient_user_id)
            if recipient_data:
                total_balance = bank_account_info['deposited_amount'] + (click_count * (bank_account_info['deposited_amount'] * 0.00005))
                if amount <= total_balance:
                    bank_account_info['deposited_amount'] -= amount
                    with connect_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute('UPDATE users SET deposited_amount = deposited_amount + ? WHERE user_id = ?', (amount, recipient_user_id))
                        conn.commit()
                    message = f"✅ Giao dịch chuyển khoản {amount} VND đến người dùng (ID: {recipient_user_id}) thành công!"
                else:
                    message = f"❌ Giao dịch chuyển khoản {amount} VND thất bại do số dư không đủ!"
            else:
                message = "❌ Người nhận không tồn tại."
        else:
            message = "❌ Không xác định được người nhận."

    await send_user_notification(user_chat_id, message)
    context.user_data['pending_transaction'] = None
    save_user_data(user_chat_id)
    await update.callback_query.answer("Giao dịch đã được phê duyệt!")

async def deny_transaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    transaction = context.user_data.get('pending_transaction')
    if transaction:
        user_chat_id = transaction['user_chat_id']
        action = transaction['action']
        amount = transaction['amount']
        message = f"❌ Giao dịch {action} tiền {amount} VND đã bị từ chối!"
        await send_user_notification(user_chat_id, message)
    context.user_data['pending_transaction'] = None

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.info("Lệnh /start đã được gọi.")

    try:
        user_id = update.message.from_user.id
        user_first_name = update.message.from_user.first_name
        user_last_name = update.message.from_user.last_name
        full_name = f"{user_first_name} {user_last_name}" if user_last_name else user_first_name

        # Khởi tạo cơ sở dữ liệu nếu chưa có
        init_db()

        # Kiểm tra xem người dùng đã có dữ liệu trong database chưa
        user_data = read_user_data(user_id)
        if user_data is None:
            bank_account_info.update({'account_number': '', 'bank_name': '', 'phone_number': ''})
            save_user_data(user_id)
        
        # Tạo bàn phím menu
        reply_keyboard = [
            ['Click để tăng lãi suất'],
            ['Nạp tiền', 'Rút tiền'],
            ['Chuyển khoản', 'Kiểm tra số dư'],
            ['Thông tin']
        ]
        reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)

        await update.message.reply_text(f'Chào {full_name}! Hãy chọn menu 👇', reply_markup=reply_markup)

    except Exception as e:
        logging.error(f"Lỗi trong start_handler: {str(e)}")
        await update.message.reply_text("❌ Đã xảy ra lỗi! Vui lòng thử lại sau.")

async def big_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global last_click_time

    current_time = time.time()
    if current_time - last_click_time < 1.5:
        await update.message.reply_text("Vui lòng chờ một chút trước khi nhấn lại!")
        return

    last_click_time = current_time
    message = "📆 Click để cập nhật thu nhập!"
    keyboard = [[InlineKeyboardButton("Cập nhật thu nhập", callback_data='update_income')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, reply_markup=reply_markup)

async def update_income_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global income_today, click_count, last_update_time
    current_time = time.time()

    if current_time - last_update_time < 2:
        await update.callback_query.answer("Vui lòng chờ một chút trước khi cập nhật!")
        return

    query = update.callback_query
    await query.answer("🤑 Cập nhật thành công!")

    deposited_amount = bank_account_info['deposited_amount']
    interest_per_click = deposited_amount * 0.00005

    income_today += interest_per_click
    click_count += 1
    last_update_time = current_time

    progress = (click_count / target_clicks) * 100

    message = (
        f"📆 Số lần nhấn hôm nay: {click_count} / {target_clicks}\n"
        f"♻️ Tiến độ: {progress:.2f} %\n"
        f"💵 Lãi suất của 1 lần click: {interest_per_click:.2f} VND\n"
        f"💰 Số tiền đã nạp vào: {deposited_amount} VND\n"
        f"💰 Thu nhập hôm nay: {income_today:.2f} VND\n"
    )

    await query.edit_message_text(text=message, reply_markup=query.message.reply_markup)

async def deposit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Vui lòng nhập số tiền bạn muốn nạp vào:")
    context.user_data['waiting_for_deposit'] = True

async def withdraw_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Vui lòng nhập số tiền bạn muốn rút:")
    context.user_data['waiting_for_withdraw'] = True

async def transfer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Vui lòng nhập ID người nhận:")
    context.user_data['waiting_for_transfer'] = True

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deposited_amount = bank_account_info['deposited_amount']
    total_income = click_count * (deposited_amount * 0.00005)
    total_balance = deposited_amount + total_income

    balance_message = (
        f"💰 Số tiền đã nạp vào: {deposited_amount} VND\n"
        f"📈 Lãi suất từ số tiền đã nạp: {total_income:.2f} VND\n"
        f"💵 Tổng số dư khả dụng: {total_balance:.2f} VND\n"
        f"💰 Số tiền đã rút: {bank_account_info['withdrawn_amount']} VND"
    )
    await update.message.reply_text(balance_message)

async def show_account_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    account_info_message = (
        "📋 Thông tin tài khoản:\n"
        f"Số tài khoản: {bank_account_info['account_number'] or 'Chưa cập nhật'}\n"
        f"Tên ngân hàng: {bank_account_info['bank_name'] or 'Chưa cập nhật'}\n"
        f"Số điện thoại: {bank_account_info['phone_number'] or 'Chưa cập nhật'}\n"
    )

    keyboard = [
        [InlineKeyboardButton("Cập nhật số tài khoản", callback_data='update_account_number')],
        [InlineKeyboardButton("Cập nhật tên ngân hàng", callback_data='update_bank_name')],
        [InlineKeyboardButton("Cập nhật số điện thoại", callback_data='update_phone_number')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(account_info_message, reply_markup=reply_markup)

async def update_account_info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    context.user_data['waiting_for_account_info'] = True
    context.user_data['update_type'] = query.data
    prompt_message = "Vui lòng nhập "
    if query.data == 'update_account_number':
        prompt_message += "số tài khoản mới:"
    elif query.data == 'update_bank_name':
        prompt_message += "tên ngân hàng mới:"
    elif query.data == 'update_phone_number':
        prompt_message += "số điện thoại mới:"
    
    await query.message.reply_text(prompt_message)

async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id  
    user_info = f"{update.message.from_user.first_name} {update.message.from_user.last_name or ''}".strip()
    user_chat_id = user_id  

    user_choice = update.message.text

    if user_choice == 'Click để tăng lãi suất':
        await big_button_handler(update, context)
    elif user_choice == 'Nạp tiền':
        await deposit_handler(update, context)
    elif user_choice == 'Rút tiền':
        await withdraw_handler(update, context)
    elif user_choice == 'Chuyển khoản':
        await transfer_handler(update, context)
    elif user_choice == 'Kiểm tra số dư':
        await check_balance(update, context)
    elif user_choice == 'Thông tin':
        await show_account_info(update, context)
    elif context.user_data.get('waiting_for_deposit'):
        try:
            amount = float(update.message.text)
            if amount <= 0:
                await update.message.reply_text("Số tiền nạp phải lớn hơn 0.")
                return
            
            await notify_admin(user_id, user_info, "nạp", amount, user_chat_id, context)
        except ValueError:
            await update.message.reply_text("Vui lòng nhập một số hợp lệ.")
        finally:
            context.user_data['waiting_for_deposit'] = False
    elif context.user_data.get('waiting_for_withdraw'):
        try:
            amount = float(update.message.text)
            if amount <= 0:
                await update.message.reply_text("Số tiền rút phải lớn hơn 0.")
                return
            
            deposited_amount = bank_account_info['deposited_amount']
            total_income = click_count * (deposited_amount * 0.00005)
            total_balance = deposited_amount + total_income
            
            if amount > total_balance:
                await update.message.reply_text("Số tiền rút không thể lớn hơn tổng số dư khả dụng.")
                return
            
            await notify_admin(user_id, user_info, "rút", amount, user_chat_id, context)
        except ValueError:
            await update.message.reply_text("Vui lòng nhập một số hợp lệ.")
        finally:
            context.user_data['waiting_for_withdraw'] = False
    elif context.user_data.get('waiting_for_transfer'):
        try:
            recipient_user_id = int(update.message.text)
            context.user_data['recipient_user_id'] = recipient_user_id
            
            recipient_data = read_user_data(recipient_user_id)
            if recipient_data:
                recipient_info_message = (
                    "🧾 Thông tin người nhận:\n"
                    f"ID: {recipient_user_id}\n"
                    f"Số tài khoản: {recipient_data['Account Number']}\n"
                    f"Tên ngân hàng: {recipient_data['Bank Name']}\n"
                    f"Số điện thoại: {recipient_data['Phone Number']}\n"
                    f"Số tiền đã nạp: {recipient_data['Deposited Amount']} VND\n"
                    f"Số tiền đã rút: {recipient_data['Withdrawn Amount']} VND\n"
                )
                await update.message.reply_text(recipient_info_message)
                await update.message.reply_text("Vui lòng nhập số tiền bạn muốn chuyển khoản:")
            else:
                await update.message.reply_text("❌ Người nhận không tồn tại.")
        except ValueError:
            await update.message.reply_text("Vui lòng nhập một ID người dùng hợp lệ.")
        finally:
            context.user_data['waiting_for_transfer'] = False
    elif 'recipient_user_id' in context.user_data:
        try:
            amount = float(update.message.text)
            if amount <= 0:
                await update.message.reply_text("Số tiền chuyển khoản phải lớn hơn 0.")
                return
            
            await notify_admin(user_id, user_info, "chuyển khoản", amount, user_chat_id, context)
        except ValueError:
            await update.message.reply_text("Vui lòng nhập một số hợp lệ.")

async def update_account_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get('waiting_for_account_info'):
        update_type = context.user_data['update_type']
        new_value = update.message.text
        
        if update_type == 'update_account_number':
            bank_account_info['account_number'] = new_value
            await update.message.reply_text("✅ Số tài khoản đã được cập nhật thành công!")
        elif update_type == 'update_bank_name':
            bank_account_info['bank_name'] = new_value
            await update.message.reply_text("✅ Tên ngân hàng đã được cập nhật thành công!")
        elif update_type == 'update_phone_number':
            bank_account_info['phone_number'] = new_value
            await update.message.reply_text("✅ Số điện thoại đã được cập nhật thành công!")

        # Lưu dữ liệu tài khoản
        user_id = update.message.from_user.id
        save_user_data(user_id)
        
        context.user_data['waiting_for_account_info'] = False

# Thêm hàm xử lý lỗi
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"Đã xảy ra lỗi: {context.error}")
    await context.bot.send_message(chat_id=admin_user_id, text=f"❌ Một lỗi đã xảy ra trong bot: {str(context.error)}")

# Khởi tạo cơ sở dữ liệu
init_db()

# Khởi tạo ứng dụng Telegram
application = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

# Đăng ký các handler
application.add_handler(CommandHandler("start", start_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_handler))
application.add_handler(CallbackQueryHandler(approve_transaction_handler, pattern='approve_transaction'))
application.add_handler(CallbackQueryHandler(deny_transaction_handler, pattern='deny_transaction'))
application.add_handler(CallbackQueryHandler(update_income_handler, pattern='update_income'))
application.add_handler(CallbackQueryHandler(update_account_info_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, update_account_info))

# Đăng ký trình xử lý lỗi
application.add_error_handler(error_handler)

# Chạy bot
if __name__ == "__main__":
    application.run_polling()
