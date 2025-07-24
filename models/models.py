from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import enum

db = SQLAlchemy()

# Enum
class BookingStatus(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CHECKED_IN = "checked_in"

class SanctionStatus(enum.Enum):
    ACTIVE = "active"
    PAID = "paid"

# Models
class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    users = db.relationship('User', backref='role_obj', lazy=True)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    bookings = db.relationship('Booking', foreign_keys='Booking.user_id', backref='user', lazy=True)
    approved_bookings = db.relationship('Booking', foreign_keys='Booking.approved_by', backref='approver', lazy=True)
    sanctions = db.relationship('Sanction', foreign_keys='Sanction.user_id', backref='user', lazy=True)
    issued_sanctions = db.relationship('Sanction', foreign_keys='Sanction.issued_by', backref='staff', lazy=True)

class Room(db.Model):
    __tablename__ = 'rooms'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    capacity = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    bookings = db.relationship('Booking', backref='room', lazy=True)

class Booking(db.Model):
    __tablename__ = 'bookings'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    booking_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    booking_dates = db.Column(db.Text, nullable=True)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    status = db.Column(db.Enum(BookingStatus), default=BookingStatus.PENDING)
    purpose = db.Column(db.Text)
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_at = db.Column(db.DateTime)
    checkout_time = db.Column(db.DateTime)
    is_late = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    checkin_time = db.Column(db.DateTime)
    checkout_time = db.Column(db.DateTime)
    is_late = db.Column(db.Boolean, default=False)

    # Index untuk meningkatkan performance query
    __table_args__ = (
        db.Index('idx_booking_date_status', 'booking_date', 'status'),
        db.Index('idx_booking_user_date', 'user_id', 'booking_date'),
        db.Index('idx_booking_room_date', 'room_id', 'booking_date'),
        db.Index('idx_checkout_time', 'checkout_time'),
        db.Index('idx_checkin_time', 'checkin_time'),
    )
    def get_booking_dates_list(self):
        import json
        if self.booking_dates:
            return json.loads(self.booking_dates)
        else:
            return [self.booking_date.strftime('%Y-%m-%d')]

    def set_booking_dates_list(self, dates_list):
        import json
        self.booking_dates = json.dumps(dates_list)
    
    def is_checkin_time(self):
        """Cek apakah sudah waktunya checkin (sudah lewat start_time)"""
        from datetime import datetime, date
        if self.booking_date != date.today():
            return False
        current_time = datetime.now().time()
        return current_time >= self.start_time
    
    def is_checkout_overdue(self):
        """Cek apakah sudah lewat waktu checkout"""
        from datetime import datetime, date
        if self.booking_date != date.today():
            return False
        current_time = datetime.now().time()
        return current_time > self.end_time
    
    def get_duration_minutes(self):
        """Hitung durasi booking dalam menit"""
        from datetime import datetime, timedelta
        start_datetime = datetime.combine(self.booking_date, self.start_time)
        end_datetime = datetime.combine(self.booking_date, self.end_time)
        duration = end_datetime - start_datetime
        return int(duration.total_seconds() / 60)
    
    def get_late_minutes(self):
        """Hitung berapa menit terlambat checkout"""
        if not self.checkout_time or not self.is_late:
            return 0
        
        from datetime import datetime
        expected_end = datetime.combine(self.booking_date, self.end_time)
        if self.checkout_time > expected_end:
            late_duration = self.checkout_time - expected_end
            return int(late_duration.total_seconds() / 60)
        return 0
    
    def can_checkin(self):
        """Cek apakah booking bisa checkin"""
        return (
            self.status == BookingStatus.APPROVED and
            self.checkin_time is None and
            self.is_checkin_time()
        )
    
    def can_checkout(self):
        """Cek apakah booking bisa checkout"""
        return (
            self.status in [BookingStatus.APPROVED, BookingStatus.CHECKED_IN] and
            self.checkout_time is None
        )
    
    def is_active(self):
        """Cek apakah booking sedang aktif (sudah checkin tapi belum checkout)"""
        return (
            self.status == BookingStatus.CHECKED_IN and
            self.checkin_time is not None and
            self.checkout_time is None
        )
    
    def get_status_display(self):
        """Get display text untuk status"""
        status_map = {
            BookingStatus.PENDING: "Menunggu Persetujuan",
            BookingStatus.APPROVED: "Disetujui",
            BookingStatus.REJECTED: "Ditolak",
            BookingStatus.CHECKED_IN: "Sedang Berlangsung",
            BookingStatus.COMPLETED: "Selesai"
        }
        return status_map.get(self.status, self.status.value)
    
    def get_status_color(self):
        """Get CSS class untuk status color"""
        color_map = {
            BookingStatus.PENDING: "warning",
            BookingStatus.APPROVED: "info",
            BookingStatus.REJECTED: "danger",
            BookingStatus.CHECKED_IN: "primary",
            BookingStatus.COMPLETED: "success"
        }
        return color_map.get(self.status, "secondary")
    
    def __repr__(self):
        return f'<Booking {self.id}: {self.user.username if self.user else "Unknown"} - {self.room.name if self.room else "Unknown"} on {self.booking_date}>'

class Sanction(db.Model):
    __tablename__ = 'sanctions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.Enum(SanctionStatus), default=SanctionStatus.ACTIVE)
    issued_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    issued_at = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at = db.Column(db.DateTime)
    booking = db.relationship('Booking', backref='sanctions')

class UserProfile(db.Model):
    __tablename__ = 'user_profiles'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    full_name = db.Column(db.String(100), nullable=False)
    prodi = db.Column(db.String(50), nullable=False)
    ktm_filename = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('profile', uselist=False))

