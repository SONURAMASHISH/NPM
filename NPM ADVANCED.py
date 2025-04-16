import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
import urllib.parse
import sqlite3
import hashlib
import yfinance as yf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib.pyplot as plt
from threading import Thread
import time
import datetime
import numpy as np
from sklearn.linear_model import LinearRegression
from PIL import Image, ImageTk
import mplfinance as mpf
import os

class DatabaseManager:
    def __init__(self):
        self.db_path = 'users.db'
        self.initialize_db()
        
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def initialize_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password TEXT NOT NULL,
                    session_token TEXT,
                    last_login TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    query TEXT NOT NULL,
                    source TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (username) REFERENCES users(username)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS watchlist (
                    username TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    UNIQUE(username, ticker)
                )
            ''')
            
            conn.commit()
    
    def hash_password(self, password):
        return hashlib.sha256(password.encode()).hexdigest()
    
    def generate_session_token(self, username):
        return hashlib.sha256(f"{username}{time.time()}".encode()).hexdigest()
    
    def signup(self, username, password):
        hashed_pwd = self.hash_password(password)
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", 
                             (username, hashed_pwd))
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def login(self, username, password):
        hashed_pwd = self.hash_password(password)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if user is already logged in elsewhere
            cursor.execute("SELECT session_token FROM users WHERE username=?", (username,))
            result = cursor.fetchone()
            if result and result[0]:
                # Invalidate old session
                cursor.execute("UPDATE users SET session_token=NULL WHERE username=?", (username,))
                conn.commit()
            
            # Create new session
            session_token = self.generate_session_token(username)
            cursor.execute("UPDATE users SET session_token=?, last_login=CURRENT_TIMESTAMP WHERE username=? AND password=?",
                         (session_token, username, hashed_pwd))
            conn.commit()
            
            cursor.execute("SELECT 1 FROM users WHERE username=? AND password=?", 
                         (username, hashed_pwd))
            return cursor.fetchone() is not None, session_token
    
    def logout(self, username):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET session_token=NULL WHERE username=?", (username,))
            conn.commit()
    
    def check_session(self, username, session_token):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE username=? AND session_token=?", 
                          (username, session_token))
            return cursor.fetchone() is not None

class StockMarketApp:
    def __init__(self, root):
        self.root = root
        self.root.title("NPM App")
        self.root.geometry("1200x800")
        
        self.db = DatabaseManager()
        self.current_user = None
        self.session_token = None
        self.auto_update_running = False
        
        # Initialize UI
        self.create_login_frame()
        self.create_main_app_frame()
        
        # Start with login screen
        self.show_login_frame()
    
    def create_login_frame(self):
        self.login_frame = tk.Frame(self.root)
        
        tk.Label(self.login_frame, text="Username").grid(row=0, column=0)
        self.username_entry = tk.Entry(self.login_frame)
        self.username_entry.grid(row=0, column=1)
        
        tk.Label(self.login_frame, text="Password").grid(row=1, column=0)
        self.password_entry = tk.Entry(self.login_frame, show="*")
        self.password_entry.grid(row=1, column=1)
        
        tk.Button(self.login_frame, text="Login", command=self.handle_login).grid(row=2, column=0, pady=10)
        tk.Button(self.login_frame, text="Sign Up", command=self.handle_signup).grid(row=2, column=1)
        
        # Bind Enter key to login
        self.root.bind('<Return>', lambda event: self.handle_login())
    
    def create_main_app_frame(self):
        self.main_frame = tk.Frame(self.root)
        self.notebook = ttk.Notebook(self.main_frame)
        self.notebook.pack(fill="both", expand=True)
        
        # Create tabs
        self.create_wikipedia_tab()
        self.create_stock_tab()
        self.create_watchlist_tab()
        self.create_history_tab()
        self.create_profile_tab()
    
    def create_wikipedia_tab(self):
        wikipedia_tab = tk.Frame(self.notebook)
        self.notebook.add(wikipedia_tab, text="Wikipedia")
        
        self.wiki_search_entry = tk.Entry(wikipedia_tab, width=50)
        self.wiki_search_entry.pack(pady=10)
        
        self.wiki_result_text = tk.Text(wikipedia_tab, wrap="word")
        self.wiki_result_text.pack(expand=True, fill="both")
        
        scrollbar = tk.Scrollbar(wikipedia_tab, command=self.wiki_result_text.yview)
        self.wiki_result_text.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        
        tk.Button(wikipedia_tab, text="Search", command=self.fetch_wikipedia).pack()
    
    def create_stock_tab(self):
        stock_tab = tk.Frame(self.notebook)
        self.notebook.add(stock_tab, text="Stock Market")
        
        self.autocomplete_var = tk.StringVar()
        self.autocomplete_entry = ttk.Combobox(stock_tab, textvariable=self.autocomplete_var, width=50)
        self.autocomplete_entry.pack(pady=10)
        self.autocomplete_entry['values'] = ["AAPL", "GOOGL", "MSFT", "AMZN", "META", "TSLA"]
        self.stock_entry = self.autocomplete_entry
        
        self.stock_output = tk.Text(stock_tab, wrap="word")
        self.stock_output.pack(expand=True, fill="both")
        
        self.chart_type = tk.StringVar(value="candlestick")
        chart_menu = ttk.Combobox(stock_tab, textvariable=self.chart_type, values=["candlestick", "line"])
        chart_menu.pack(pady=5)
        
        self.stock_canvas = None
        self.stock_toolbar = None
        self.candlestick_fig = None
        self.current_ticker = ""
        
        button_frame = tk.Frame(stock_tab)
        button_frame.pack(pady=5)
        
        tk.Button(button_frame, text="Fetch Stock", command=self.fetch_stock_button).pack(side="left", padx=5)
        tk.Button(button_frame, text="Add to Watchlist", command=self.add_to_watchlist).pack(side="left", padx=5)
        
        # Prediction section
        prediction_frame = tk.Frame(stock_tab)
        prediction_frame.pack(pady=5)
        
        self.prediction_var = tk.StringVar(value="1 Day")
        prediction_options = ["1 Day", "2 Days", "5 Days", "5 Months"]
        ttk.OptionMenu(prediction_frame, self.prediction_var, prediction_options[0], *prediction_options).pack(side="left", padx=5)
        tk.Button(prediction_frame, text="Predict Future Price", command=self.predict_stock).pack(side="left", padx=5)
    
    def create_watchlist_tab(self):
        watchlist_tab = tk.Frame(self.notebook)
        self.notebook.add(watchlist_tab, text="Watchlist")
        
        self.watchlist_listbox = tk.Listbox(watchlist_tab)
        self.watchlist_listbox.pack(fill="both", expand=True, padx=10, pady=10)
        
        button_frame = tk.Frame(watchlist_tab)
        button_frame.pack(pady=5)
        
        tk.Button(button_frame, text="Refresh", command=self.refresh_watchlist).pack(side="left", padx=5)
        tk.Button(button_frame, text="View", command=self.view_watchlist_stock).pack(side="left", padx=5)
        tk.Button(button_frame, text="Remove", command=self.remove_from_watchlist).pack(side="left", padx=5)
    
    def create_history_tab(self):
        history_tab = tk.Frame(self.notebook)
        self.notebook.add(history_tab, text="History")
        
        self.history_tree = ttk.Treeview(history_tab, columns=("query", "source", "timestamp"), show="headings")
        self.history_tree.heading("query", text="Query")
        self.history_tree.heading("source", text="Source")
        self.history_tree.heading("timestamp", text="Timestamp")
        self.history_tree.column("query", width=200)
        self.history_tree.column("source", width=100)
        self.history_tree.column("timestamp", width=150)
        
        scrollbar = ttk.Scrollbar(history_tab, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=scrollbar.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        button_frame = tk.Frame(history_tab)
        button_frame.pack(pady=5)
        
        tk.Button(button_frame, text="Refresh", command=self.refresh_history).pack(side="left", padx=5)
        tk.Button(button_frame, text="Clear", command=self.clear_history).pack(side="left", padx=5)
    
    def create_profile_tab(self):
        profile_tab = tk.Frame(self.notebook)
        self.notebook.add(profile_tab, text="Profile")
        
        self.profile_frame = tk.Frame(profile_tab)
        self.profile_frame.pack(pady=20)
        
        self.profile_image_label = tk.Label(self.profile_frame)
        self.profile_image_label.pack()
        
        self.profile_image_path = tk.StringVar()
        
        tk.Button(self.profile_frame, text="Change Profile Image", command=self.update_profile_image).pack(pady=10)
        
        self.username_label = tk.Label(self.profile_frame, text="")
        self.username_label.pack()
        
        tk.Button(self.profile_frame, text="Logout", command=self.logout).pack(pady=20)
    
    def show_login_frame(self):
        self.main_frame.pack_forget()
        self.login_frame.pack(pady=100)
        self.username_entry.focus_set()
    
    def show_main_app(self):
        self.login_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)
        self.refresh_watchlist()
        self.refresh_history()
        self.update_profile_display()
    
    def handle_login(self):
        username = self.username_entry.get()
        password = self.password_entry.get()
        
        if not username or not password:
            messagebox.showerror("Error", "Username and password are required")
            return
        
        success, session_token = self.db.login(username, password)
        if success:
            self.current_user = username
            self.session_token = session_token
            self.show_main_app()
        else:
            messagebox.showerror("Login Failed", "Invalid credentials")
    
    def handle_signup(self):
        username = self.username_entry.get()
        password = self.password_entry.get()
        
        if not username or not password:
            messagebox.showerror("Error", "Username and password are required")
            return
        
        if self.db.signup(username, password):
            messagebox.showinfo("Success", "Account created! Please login.")
        else:
            messagebox.showerror("Error", "Username already exists.")
    
    def logout(self):
        if self.current_user:
            self.db.logout(self.current_user)
            self.auto_update_running = False
            self.current_user = None
            self.session_token = None
            self.show_login_frame()
    
    def fetch_wikipedia(self):
        try:
            query = self.wiki_search_entry.get()
            if not query:
                raise ValueError("Enter a search term")

            url = f"https://en.wikipedia.org/w/api.php?action=query&format=json&prop=extracts&exintro&explaintext&titles={urllib.parse.quote_plus(query)}"
            response = requests.get(url)
            if response.status_code != 200:
                raise Exception("Failed to fetch data from Wikipedia")

            data = response.json()
            page = next(iter(data['query']['pages'].values()))
            extract = page.get('extract', 'No content available.')

            self.wiki_result_text.delete(1.0, tk.END)
            self.wiki_result_text.insert(tk.END, extract)

            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO search_history (username, query, source) VALUES (?, ?, ?)",
                    (self.current_user, query, "Wikipedia")
                )
                conn.commit()
        except Exception as e:
            self.wiki_result_text.delete(1.0, tk.END)
            self.wiki_result_text.insert(tk.END, f"Error: {e}")
    
    def fetch_stock(self, auto=False):
        if not self.db.check_session(self.current_user, self.session_token):
            self.logout()
            messagebox.showerror("Session Expired", "You have been logged out from another device")
            return

        if self.stock_canvas:
            self.stock_canvas.get_tk_widget().destroy()
        if self.stock_toolbar:
            self.stock_toolbar.destroy()

        ticker = self.stock_entry.get().upper()
        if not ticker:
            return
        
        self.current_ticker = ticker
        
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d", interval="1m")
            if hist.empty:
                raise Exception("No intraday data found.")

            current_price = stock.info.get("regularMarketPrice", "N/A")
            self.stock_output.delete(1.0, tk.END)
            self.stock_output.insert(tk.END, f"Stock: {ticker}\nCurrent Price: {current_price}\n")

            if self.chart_type.get() == "line":
                fig, ax = plt.subplots()
                hist['Close'].plot(ax=ax)
                ax.set_title(f"{ticker} Price Line Chart")
                self.candlestick_fig = fig
            else:
                mpf_fig, mpf_ax = mpf.plot(hist, type='candle', style='charles', returnfig=True)
                self.candlestick_fig = mpf_fig

            self.stock_canvas = FigureCanvasTkAgg(self.candlestick_fig, master=self.notebook.nametowidget(self.notebook.select()))
            self.stock_canvas.draw()

            self.stock_toolbar = NavigationToolbar2Tk(self.stock_canvas, self.notebook.nametowidget(self.notebook.select()))
            self.stock_toolbar.update()
            self.stock_toolbar.pack(side="top", fill="x")

            self.stock_canvas.get_tk_widget().pack(fill="both", expand=True)

        except Exception as e:
            if not auto:
                self.stock_output.delete(1.0, tk.END)
                self.stock_output.insert(tk.END, f"Error fetching stock: {e}")
    
    def auto_update_stock(self):
        while self.auto_update_running:
            time.sleep(60)
            if self.auto_update_running:  # Check again in case it changed during sleep
                self.root.after(0, lambda: self.fetch_stock(auto=True))
    
    def start_auto_update_thread(self):
        if not self.auto_update_running:
            self.auto_update_running = True
            thread = Thread(target=self.auto_update_stock, daemon=True)
            thread.start()
    
    def fetch_stock_button(self):
        self.fetch_stock()
        self.start_auto_update_thread()
    
    def add_to_watchlist(self):
        if not self.db.check_session(self.current_user, self.session_token):
            self.logout()
            messagebox.showerror("Session Expired", "You have been logged out from another device")
            return

        ticker = self.stock_entry.get().upper()
        if ticker:
            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("INSERT OR IGNORE INTO watchlist (username, ticker) VALUES (?, ?)", 
                                (self.current_user, ticker))
                    conn.commit()
                messagebox.showinfo("Success", f"{ticker} added to watchlist")
                self.refresh_watchlist()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to add to watchlist: {e}")
    
    def predict_stock(self):
        if not self.db.check_session(self.current_user, self.session_token):
            self.logout()
            messagebox.showerror("Session Expired", "You have been logged out from another device")
            return

        ticker = self.stock_entry.get().upper()
        prediction_type = self.prediction_var.get()
        period_map = {
            "1 Day": ("1y", 1), 
            "2 Days": ("1y", 2), 
            "5 Days": ("1y", 5), 
            "5 Months": ("5y", 30 * 5)
        }
        history_period, predict_days = period_map[prediction_type]

        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=history_period)
            if hist.empty:
                raise Exception("No historical data")

            hist = hist.reset_index()
            hist["DateOrdinal"] = hist["Date"].map(datetime.datetime.toordinal)

            X = np.array(hist["DateOrdinal"]).reshape(-1, 1)
            y = hist["Close"].values

            model = LinearRegression()
            model.fit(X, y)

            future_date = hist["Date"].max() + datetime.timedelta(days=predict_days)
            future_ordinal = future_date.toordinal()
            pred_price = model.predict([[future_ordinal]])[0]

            self.stock_output.insert(tk.END, f"\nPredicted Price in {prediction_type}: ${pred_price:.2f}\n")

            # AI-like assistant message
            last_price = y[-1]
            diff = pred_price - last_price
            if diff > 2:
                suggestion = "AI Suggestion: 📈 Looks like it might rise. Consider holding or buying."
            elif diff < -2:
                suggestion = "AI Suggestion: 📉 Might drop. Caution advised."
            else:
                suggestion = "AI Suggestion: ⚖️ Stable outlook. Monitor regularly."

            self.stock_output.insert(tk.END, suggestion + "\n")

        except Exception as e:
            self.stock_output.insert(tk.END, f"\nPrediction Error: {e}\n")
    
    def refresh_watchlist(self):
        if not self.current_user:
            return
            
        if not self.db.check_session(self.current_user, self.session_token):
            self.logout()
            messagebox.showerror("Session Expired", "You have been logged out from another device")
            return

        self.watchlist_listbox.delete(0, tk.END)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ticker FROM watchlist WHERE username=?", (self.current_user,))
            for row in cursor.fetchall():
                self.watchlist_listbox.insert(tk.END, row[0])
    
    def remove_from_watchlist(self):
        if not self.db.check_session(self.current_user, self.session_token):
            self.logout()
            messagebox.showerror("Session Expired", "You have been logged out from another device")
            return

        selection = self.watchlist_listbox.curselection()
        if selection:
            ticker = self.watchlist_listbox.get(selection[0])
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM watchlist WHERE username=? AND ticker=?", 
                            (self.current_user, ticker))
                conn.commit()
            self.refresh_watchlist()
    
    def view_watchlist_stock(self):
        selection = self.watchlist_listbox.curselection()
        if selection:
            ticker = self.watchlist_listbox.get(selection[0])
            self.stock_entry.delete(0, tk.END)
            self.stock_entry.insert(0, ticker)
            self.notebook.select(1)  # Select stock tab
            self.fetch_stock()
    
    def refresh_history(self):
        if not self.current_user:
            return
            
        if not self.db.check_session(self.current_user, self.session_token):
            self.logout()
            messagebox.showerror("Session Expired", "You have been logged out from another device")
            return

        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
            
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT query, source, timestamp FROM search_history WHERE username=? ORDER BY timestamp DESC", 
                         (self.current_user,))
            for row in cursor.fetchall():
                self.history_tree.insert("", "end", values=row)
    
    def clear_history(self):
        if not self.db.check_session(self.current_user, self.session_token):
            self.logout()
            messagebox.showerror("Session Expired", "You have been logged out from another device")
            return

        if messagebox.askyesno("Confirm", "Clear all search history?"):
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM search_history WHERE username=?", (self.current_user,))
                conn.commit()
            self.refresh_history()
    
    def update_profile_image(self):
        if not self.db.check_session(self.current_user, self.session_token):
            self.logout()
            messagebox.showerror("Session Expired", "You have been logged out from another device")
            return

        filepath = filedialog.askopenfilename(filetypes=[("Image Files", "*.png *.jpg *.jpeg")])
        if filepath:
            try:
                image = Image.open(filepath)
                image.thumbnail((150, 150))
                photo = ImageTk.PhotoImage(image)
                self.profile_image_label.config(image=photo)
                self.profile_image_label.image = photo
                self.profile_image_path.set(filepath)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load image: {e}")
    
    def update_profile_display(self):
        if self.current_user:
            self.username_label.config(text=f"Logged in as: {self.current_user}")
    
    def on_closing(self):
        self.auto_update_running = False
        if self.current_user:
            self.db.logout(self.current_user)
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = StockMarketApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
