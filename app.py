from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask.cli import with_appcontext
from datetime import datetime, date, time, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from sqlalchemy import and_, or_

import enum
import click
import re
import os
import json


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:root@localhost:3306/room_booking'
app.config['SECRET_KEY'] = 'Papua123'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['UPLOAD_FOLDER'] = 'static/uploads/ktm'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Enums
class BookingStatus(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    

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

# Models - Modifikasi class Booking
class Booking(db.Model):
    __tablename__ = 'bookings'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    booking_date = db.Column(db.Date, nullable=False)  # Tanggal mulai booking
    end_date = db.Column(db.Date, nullable=True)  # Tanggal akhir booking (untuk multi-day)
    booking_dates = db.Column(db.Text, nullable=True)  # JSON string untuk menyimpan semua tanggal
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    status = db.Column(db.Enum(BookingStatus), default=BookingStatus.PENDING)
    purpose = db.Column(db.Text)
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    approved_at = db.Column(db.DateTime)
    checkout_time = db.Column(db.DateTime)
    is_late = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_booking_dates_list(self):
        """Helper method untuk mendapatkan list tanggal booking"""
        if self.booking_dates:
            import json
            return json.loads(self.booking_dates)
        else:
            # Fallback untuk single date
            return [self.booking_date.strftime('%Y-%m-%d')]
    
    def set_booking_dates_list(self, dates_list):
        """Helper method untuk set list tanggal booking"""
        import json
        self.booking_dates = json.dumps(dates_list)

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

# CLI command: flask init-data
@click.command('init-data')
@with_appcontext
def init_data():
    """Inisialisasi data role dan admin user"""
    roles = [
        {'name': 'admin', 'description': 'Administrator with full access'},
        {'name': 'staff', 'description': 'Staff with limited access'},
        {'name': 'member', 'description': 'Standard user'}
    ]
    for role in roles:
        if not Role.query.filter_by(name=role['name']).first():
            db.session.add(Role(name=role['name'], description=role['description']))
            print(f"✅ Role '{role['name']}' ditambahkan.")
    db.session.commit()

    admin_role = Role.query.filter_by(name='admin').first()
    if admin_role and not User.query.filter_by(username='admin').first():
        admin_user = User(
            username='admin',
            email='admin@example.com',
            password_hash=generate_password_hash('admin123'),
            role_id=admin_role.id
        )
        db.session.add(admin_user)
        db.session.commit()
        print("✅ Admin user berhasil dibuat.")
        print("Username: admin | Password: admin123")
    else:
        print("ℹ️ Admin user sudah ada.")

# Daftarkan command CLI ke Flask
app.cli.add_command(init_data)



###########################################################################################################

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'pdf'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def home():
    rooms = Room.query.filter_by(is_active=True).all()
    total_rooms = Room.query.count()
    today = datetime.now().date()
    
    rooms_with_status = []
    for room in rooms:
        # Query booking tanpa join
        today_booking = Booking.query.filter(
            and_(
                Booking.room_id == room.id,
                Booking.booking_date == today,
                or_(
                    Booking.status == 'APPROVED',
                    Booking.status == 'PENDING'
                )
            )
        ).first()
        
        room_data = {
            'id': room.id,
            'name': room.name,
            'description': room.description,
            'capacity': room.capacity,
            'is_booked_today': today_booking is not None,
            'today_booking_time': None,
            'booker_name': None,
            'booker_prodi': None,
            'booking_status': None
        }
        
        if today_booking:
            # Get user and their profile
            user = User.query.get(today_booking.user_id)
            user_profile = None
            if user:
                # Get user profile using the relationship
                user_profile = user.profile  # Using the backref defined in UserProfile model
            
            room_data.update({
                'today_booking_time': f"{today_booking.start_time.strftime('%H:%M')} - {today_booking.end_time.strftime('%H:%M')}",
                'booker_name': user_profile.full_name if user_profile else (user.username if user else 'Unknown'),
                'booker_prodi': user_profile.prodi if user_profile else 'Unknown',
                'booking_status': today_booking.status
            })
        
        rooms_with_status.append(room_data)
    
    return render_template('home.html', rooms=rooms_with_status, total_rooms=total_rooms)

@app.route('/room_schedule/<int:room_id>')
def room_schedule(room_id):
    """Endpoint untuk mendapatkan jadwal room dalam format JSON"""
    room = Room.query.get_or_404(room_id)
    
    # Generate 21 hari dari hari ini (termasuk 2 hari sebelumnya)
    start_date = datetime.now().date() - timedelta(days=2)
    dates_schedule = []
    
    for i in range(24):  # 2 hari sebelumnya + 22 hari ke depan
        current_date = start_date + timedelta(days=i)
        
        # Cek booking untuk tanggal ini
        bookings = Booking.query.filter(
            and_(
                Booking.room_id == room_id,
                Booking.booking_date == current_date,
                or_(
                    Booking.status == 'confirmed',
                    Booking.status == 'pending'
                )
            )
        ).all()
        
        # Format data untuk frontend
        booking_details = []
        for booking in bookings:
            booking_details.append({
                'id': booking.id,
                'start_time': booking.start_time.strftime('%H:%M'),
                'end_time': booking.end_time.strftime('%H:%M'),
                'purpose': booking.purpose,
                'status': booking.status,
                'user_name': booking.user.username if booking.user else 'Unknown'
            })
        
        dates_schedule.append({
            'date': current_date.strftime('%Y-%m-%d'),
            'date_formatted': current_date.strftime('%d %b %Y'),
            'day_name': current_date.strftime('%A'),
            'is_today': current_date == datetime.now().date(),
            'is_past': current_date < datetime.now().date(),
            'is_booked': len(bookings) > 0,
            'booking_count': len(bookings),
            'bookings': booking_details
        })
    
    return jsonify({
        'room': {
            'id': room.id,
            'name': room.name,
            'capacity': room.capacity,
            'description': room.description
        },
        'schedule': dates_schedule
    })

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        user = User.query.filter_by(username=username).first()

        if not user or not check_password_hash(user.password_hash, password):
            error = 'Username atau password salah'
        elif not user.is_active:
            error = 'Akun tidak aktif'
        else:
            # Simpan user ID ke session
            session['user_id'] = user.id
            session['role_id'] = user.role_id

            # Redirect berdasarkan role
            if user.role_id == 1:
                return redirect(url_for('admin_dashboard'))
            elif user.role_id == 2:
                return redirect(url_for('staff_dashboard'))
            elif user.role_id == 3:
                return redirect(url_for('member_dashboard'))
            else:
                error = 'Role tidak dikenal'

    return render_template('login.html', error=error)

def staff_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        
        user = User.query.get(session['user_id'])
        if not user or user.role_id != 2:  # role_id 2 = staff
            flash('Akses ditolak. Hanya staff yang dapat mengakses halaman ini.', 'error')
            return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    return decorated_function

@app.route('/staff/dashboard')
@staff_required
def staff_dashboard():
    # Ambil semua booking yang perlu dikelola (pending, approved, completed, cancelled)
    recent_bookings = Booking.query\
        .join(User, Booking.user_id == User.id)\
        .join(Room, Booking.room_id == Room.id)\
        .add_columns(
            User.username,
            User.email,
            Room.name.label('room_name')
        )\
        .order_by(Booking.created_at.desc())\
        .limit(20).all()  # Tingkatkan limit menjadi 20
    
    # Kirim fungsi today ke template untuk menghitung perbedaan hari
    return render_template('staff/dashboard.html', 
                         recent_bookings=recent_bookings,
                         today=date.today)


@app.route('/staff/approve-booking/<int:booking_id>', methods=['POST'])
@staff_required
def approve_booking(booking_id):
    """Route untuk menyetujui booking"""
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Cek apakah booking masih dalam status pending
        if booking.status != BookingStatus.PENDING:
            return jsonify({
                'success': False,
                'message': 'Booking sudah diproses sebelumnya'
            }), 400
        
        # Cek apakah ada konflik waktu dengan booking yang sudah disetujui
        conflicting_booking = Booking.query.filter(
            Booking.room_id == booking.room_id,
            Booking.booking_date == booking.booking_date,
            Booking.status == BookingStatus.APPROVED,
            Booking.id != booking_id,
            db.or_(
                db.and_(
                    Booking.start_time <= booking.start_time,
                    Booking.end_time > booking.start_time
                ),
                db.and_(
                    Booking.start_time < booking.end_time,
                    Booking.end_time >= booking.end_time
                ),
                db.and_(
                    Booking.start_time >= booking.start_time,
                    Booking.end_time <= booking.end_time
                )
            )
        ).first()
        
        if conflicting_booking:
            return jsonify({
                'success': False,
                'message': 'Ada booking lain yang sudah disetujui pada waktu yang sama'
            }), 400
        
        # Update status booking
        booking.status = BookingStatus.APPROVED
        booking.approved_by = session['user_id']
        booking.approved_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Booking berhasil disetujui'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Terjadi kesalahan: {str(e)}'
        }), 500

@app.route('/staff/reject-booking/<int:booking_id>', methods=['POST'])
@staff_required
def reject_booking(booking_id):
    """Route untuk menolak booking"""
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Cek apakah booking masih dalam status pending
        if booking.status != BookingStatus.PENDING:
            return jsonify({
                'success': False,
                'message': 'Booking sudah diproses sebelumnya'
            }), 400
        
        # Ambil alasan penolakan dari request
        data = request.get_json()
        reason = data.get('reason', '') if data else ''
        
        if not reason or reason.strip() == '':
            return jsonify({
                'success': False,
                'message': 'Alasan penolakan harus diisi'
            }), 400
        
        # Update status booking
        booking.status = BookingStatus.CANCELLED
        booking.approved_by = session['user_id']
        booking.approved_at = datetime.utcnow()
        
        # Simpan alasan penolakan di purpose (atau buat field rejection_reason jika perlu)
        if booking.purpose:
            booking.purpose += f"\n\n[DITOLAK] Alasan: {reason}"
        else:
            booking.purpose = f"[DITOLAK] Alasan: {reason}"
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Booking berhasil ditolak'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Terjadi kesalahan: {str(e)}'
        }), 500

@app.route('/staff/cancel-booking/<int:booking_id>', methods=['POST'])
@staff_required
def cancel_booking(booking_id):
    """Route untuk membatalkan booking yang sudah disetujui"""
    try:
        booking = Booking.query.get_or_404(booking_id)
        
        # Cek apakah booking dalam status approved
        if booking.status != BookingStatus.APPROVED:
            return jsonify({
                'success': False,
                'message': 'Hanya booking yang sudah disetujui yang dapat dibatalkan'
            }), 400
        
        # Cek apakah booking sudah dimulai (opsional)
        current_datetime = datetime.now()
        booking_datetime = datetime.combine(booking.booking_date, booking.start_time)
        
        if current_datetime >= booking_datetime:
            return jsonify({
                'success': False,
                'message': 'Tidak dapat membatalkan booking yang sudah dimulai'
            }), 400
        
        # Update status booking
        booking.status = BookingStatus.REJECTED
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Booking berhasil dibatalkan'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Terjadi kesalahan: {str(e)}'
        }), 500
    
@app.route('/staff/booking/<int:booking_id>')
@staff_required
def booking_detail(booking_id):
    """Route untuk melihat detail booking"""
    booking = Booking.query.filter_by(id=booking_id)\
        .join(User, Booking.user_id == User.id)\
        .join(Room, Booking.room_id == Room.id)\
        .add_columns(
            User.username,
            User.email,
            Room.name.label('room_name'),
            Room.description.label('room_description'),
            Room.capacity.label('room_capacity')
        ).first_or_404()
    
    # Ambil data approver jika ada
    approver = None
    if booking.Booking.approved_by:
        approver = User.query.get(booking.Booking.approved_by)
    
    # Ambil profil user jika ada
    user_profile = UserProfile.query.filter_by(user_id=booking.Booking.user_id).first()
    
    # Ambil data sanksi jika ada
    sanctions = Sanction.query.filter_by(booking_id=booking_id).all()
    
    return render_template('staff/booking_detail.html',
                         booking=booking,
                         approver=approver,
                         user_profile=user_profile,
                         sanctions=sanctions)

@app.route('/staff/bookings')
@staff_required
def staff_bookings():
    """Route untuk melihat semua booking dengan filter"""
    # Ambil parameter filter dari query string
    status_filter = request.args.get('status', 'all')
    date_filter = request.args.get('date', 'all')
    room_filter = request.args.get('room', 'all')
    
    # Base query
    query = Booking.query.join(User, Booking.user_id == User.id)\
                        .join(Room, Booking.room_id == Room.id)\
                        .add_columns(
                            User.username,
                            User.email,
                            Room.name.label('room_name')
                        )
    
    # Apply filters
    if status_filter != 'all':
        if status_filter == 'pending':
            query = query.filter(Booking.status == BookingStatus.PENDING)
        elif status_filter == 'approved':
            query = query.filter(Booking.status == BookingStatus.APPROVED)
        elif status_filter == 'completed':
            query = query.filter(Booking.status == BookingStatus.COMPLETED)
        elif status_filter == 'cancelled':
            query = query.filter(Booking.status == BookingStatus.CANCELLED)
    
    if date_filter == 'today':
        query = query.filter(Booking.booking_date == date.today())
    elif date_filter == 'tomorrow':
        query = query.filter(Booking.booking_date == date.today() + timedelta(days=1))
    elif date_filter == 'this_week':
        today = date.today()
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        query = query.filter(Booking.booking_date.between(start_week, end_week))
    
    if room_filter != 'all':
        query = query.filter(Room.id == int(room_filter))
    
    # Order by created_at desc
    bookings = query.order_by(Booking.created_at.desc()).all()
    
    # Ambil data ruangan untuk filter dropdown
    rooms = Room.query.filter_by(is_active=True).all()
    
    return render_template('staff/bookings.html',
                         bookings=bookings,
                         rooms=rooms,
                         current_filters={
                             'status': status_filter,
                             'date': date_filter,
                             'room': room_filter
                         },
                         today=date.today)



# Decorator untuk proteksi member
def member_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        
        user = User.query.get(session['user_id'])
        if not user or user.role_id != 3:  # role_id 3 = member
            flash('Akses ditolak. Hanya member yang dapat mengakses halaman ini.', 'error')
            return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    return decorated_function

# Update pada route member_dashboard
@app.route('/member/dashboard')
@member_required
def member_dashboard():
    user = User.query.get(session['user_id'])
    
    # Ambil statistik member
    total_bookings = Booking.query.filter_by(user_id=user.id).count()
    approved_bookings = Booking.query.filter_by(user_id=user.id, status=BookingStatus.APPROVED).count()
    pending_bookings = Booking.query.filter_by(user_id=user.id, status=BookingStatus.PENDING).count()
    active_sanctions = Sanction.query.filter_by(user_id=user.id, status=SanctionStatus.ACTIVE).count()
    
    # Ambil semua room yang aktif
    rooms = Room.query.filter_by(is_active=True).all()
    
    # Ambil booking terbaru user
    recent_bookings = Booking.query.filter_by(user_id=user.id).order_by(
        Booking.created_at.desc()
    ).limit(5).all()
    
    member_stats = {
        'total_bookings': total_bookings,
        'approved_bookings': approved_bookings,
        'pending_bookings': pending_bookings,
        'active_sanctions': active_sanctions
    }
    
    return render_template('member/dashboard.html', 
                         user=user,
                         rooms=rooms, 
                         member_stats=member_stats,
                         recent_bookings=recent_bookings)

@app.route('/member/profile', methods=['GET', 'POST'])
@member_required
def member_profile():
    user = User.query.get(session['user_id'])
    profile = UserProfile.query.filter_by(user_id=user.id).first()
    error = None
    success = None
    
    # Daftar program studi
    prodi_options = [
        'Ilmu Ekonomi',
        'Ilmu Akuntansi', 
        'Manajemen'
    ]
    
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        prodi = request.form.get('prodi', '').strip()
        ktm_file = request.files.get('ktm_file')
        
        # Validasi input
        if not full_name or not prodi:
            error = 'Nama lengkap dan program studi wajib diisi.'
        elif prodi not in prodi_options:
            error = 'Program studi tidak valid.'
        elif len(full_name) < 2:
            error = 'Nama lengkap minimal 2 karakter.'
        elif len(full_name) > 100:
            error = 'Nama lengkap maksimal 100 karakter.'
        else:
            try:
                # Handle file upload
                ktm_filename = None
                if ktm_file and ktm_file.filename:
                    if not allowed_file(ktm_file.filename):
                        error = 'File KTM harus berformat PDF.'
                    else:
                        # Generate unique filename
                        filename = secure_filename(ktm_file.filename)
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        ktm_filename = f"{user.id}_{timestamp}_{filename}"
                        
                        # Save file
                        file_path = os.path.join(app.config['UPLOAD_FOLDER'], ktm_filename)
                        ktm_file.save(file_path)
                
                if not error:
                    if profile:
                        # Update existing profile
                        # Hapus file lama jika ada file baru
                        if ktm_filename and profile.ktm_filename:
                            old_file_path = os.path.join(app.config['UPLOAD_FOLDER'], profile.ktm_filename)
                            if os.path.exists(old_file_path):
                                os.remove(old_file_path)
                        
                        profile.full_name = full_name
                        profile.prodi = prodi
                        profile.updated_at = datetime.utcnow()
                        
                        # Update filename jika ada file baru
                        if ktm_filename:
                            profile.ktm_filename = ktm_filename
                        
                        success = 'Profile berhasil diperbarui!'
                    else:
                        # Create new profile
                        profile = UserProfile(
                            user_id=user.id,
                            full_name=full_name,
                            prodi=prodi,
                            ktm_filename=ktm_filename
                        )
                        db.session.add(profile)
                        success = 'Profile berhasil dibuat!'
                    
                    db.session.commit()
                    
            except Exception as e:
                error = f'Terjadi kesalahan: {str(e)}'
                # Hapus file yang sudah diupload jika terjadi error
                if ktm_filename:
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], ktm_filename)
                    if os.path.exists(file_path):
                        os.remove(file_path)
    
    return render_template('member/profile.html', 
                         user=user, 
                         profile=profile, 
                         prodi_options=prodi_options,
                         error=error, 
                         success=success)

@app.route('/member/profile/download-ktm')
@member_required
def member_download_ktm():
    user = User.query.get(session['user_id'])
    profile = UserProfile.query.filter_by(user_id=user.id).first()
    
    if not profile or not profile.ktm_filename:
        flash('File KTM tidak ditemukan.', 'error')
        return redirect(url_for('member_profile'))
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], profile.ktm_filename)
    
    if not os.path.exists(file_path):
        flash('File KTM tidak ditemukan di server.', 'error')
        return redirect(url_for('member_profile'))
    
    return send_file(file_path, as_attachment=True, download_name=f"KTM_{profile.full_name}.pdf")

@app.route('/member/profile/delete-ktm', methods=['POST'])
@member_required
def member_delete_ktm():
    user = User.query.get(session['user_id'])
    profile = UserProfile.query.filter_by(user_id=user.id).first()
    
    if not profile or not profile.ktm_filename:
        flash('File KTM tidak ditemukan.', 'error')
        return redirect(url_for('member_profile'))
    
    # Hapus file dari server
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], profile.ktm_filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    
    # Hapus referensi dari database
    profile.ktm_filename = None
    profile.updated_at = datetime.utcnow()
    db.session.commit()
    
    flash('File KTM berhasil dihapus.', 'success')
    return redirect(url_for('member_profile'))


# Route yang dimodifikasi
@app.route('/member/book/<int:room_id>', methods=['GET', 'POST'])
@member_required
def member_book_room(room_id):
    room = Room.query.get_or_404(room_id)
    user = User.query.get(session['user_id'])
    error = None
    success = None
    
    # Cek apakah room aktif
    if not room.is_active:
        flash('Ruangan tidak tersedia untuk booking!', 'error')
        return redirect(url_for('member_dashboard'))
    
    # Cek apakah user memiliki sanksi aktif
    active_sanctions = Sanction.query.filter_by(
        user_id=user.id, 
        status=SanctionStatus.ACTIVE
    ).count()
    
    if active_sanctions > 0:
        flash('Anda tidak dapat melakukan booking karena memiliki sanksi aktif. Silakan selesaikan pembayaran sanksi terlebih dahulu.', 'error')
        return redirect(url_for('member_dashboard'))
    
    # Cek apakah user sudah memiliki booking dengan status APPROVED yang belum COMPLETED
    active_booking = Booking.query.filter(
        Booking.user_id == user.id,
        Booking.status == BookingStatus.APPROVED
    ).first()
    
    if active_booking:
        flash('Anda tidak dapat melakukan booking karena masih memiliki booking yang disetujui dan belum selesai. Satu member hanya dapat memiliki 1 booking aktif.', 'error')
        return redirect(url_for('member_dashboard'))
    
    if request.method == 'POST':
        selected_dates = request.form.get('selected_dates', '').strip()
        purpose = request.form.get('purpose', '').strip()
        
        # Validasi input
        if not selected_dates or not purpose:
            error = 'Tanggal dan keperluan wajib diisi.'
        else:
            try:
                # Parse selected dates
                date_strings = [date.strip() for date in selected_dates.split(',') if date.strip()]
                
                if len(date_strings) == 0:
                    error = 'Pilih minimal 1 tanggal untuk booking.'
                elif len(date_strings) > 7:
                    error = 'Maksimal 7 hari yang dapat dipilih sekaligus.'
                else:
                    # Konversi string ke date objects dan validasi
                    booking_dates = []
                    today = datetime.now().date()
                    min_booking_date = today + timedelta(days=2)  # Minimal 2 hari dari sekarang
                    
                    for date_str in date_strings:
                        booking_date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                        
                        # Validasi tanggal tidak boleh kurang dari 2 hari dari sekarang
                        if booking_date_obj < min_booking_date:
                            error = 'Booking minimal 2 hari dari sekarang.'
                            break
                        
                        booking_dates.append(booking_date_obj)
                    
                    if not error:
                        # Sort tanggal
                        booking_dates.sort()
                        
                        # Fixed time sesuai HTML: 08:30 - 15:40
                        start_time_obj = time(8, 30)  # 08:30
                        end_time_obj = time(15, 40)   # 15:40
                        
                        # Cek konflik jadwal untuk setiap tanggal
                        conflicting_dates = []
                        for booking_date_obj in booking_dates:
                            # Cek booking existing yang overlap dengan tanggal ini
                            existing_bookings = Booking.query.filter(
                                Booking.room_id == room_id,
                                Booking.status.in_([BookingStatus.PENDING, BookingStatus.APPROVED])
                            ).all()
                            
                            for existing_booking in existing_bookings:
                                existing_dates = existing_booking.get_booking_dates_list()
                                existing_dates = [datetime.strptime(d, '%Y-%m-%d').date() for d in existing_dates]
                                
                                if booking_date_obj in existing_dates:
                                    conflicting_dates.append(booking_date_obj.strftime('%d/%m/%Y'))
                                    break
                        
                        if conflicting_dates:
                            error = f'Tanggal berikut sudah dibooking: {", ".join(set(conflicting_dates))}.'
                        else:
                            # Double check: Pastikan user tidak memiliki booking APPROVED yang aktif
                            user_approved_booking = Booking.query.filter(
                                Booking.user_id == user.id,
                                Booking.status == BookingStatus.APPROVED
                            ).first()
                            
                            if user_approved_booking:
                                error = 'Anda sudah memiliki booking yang disetujui dan belum selesai. Satu member hanya dapat memiliki 1 booking aktif.'
                            else:
                                # Buat satu booking untuk semua tanggal yang dipilih
                                try:
                                    # Convert dates to string list
                                    dates_str_list = [date.strftime('%Y-%m-%d') for date in booking_dates]
                                    
                                    new_booking = Booking(
                                        user_id=user.id,
                                        room_id=room_id,
                                        booking_date=booking_dates[0],  # Tanggal mulai
                                        end_date=booking_dates[-1],     # Tanggal akhir
                                        start_time=start_time_obj,
                                        end_time=end_time_obj,
                                        purpose=purpose,
                                        status=BookingStatus.PENDING
                                    )
                                    
                                    # Set booking dates menggunakan helper method
                                    new_booking.set_booking_dates_list(dates_str_list)
                                    
                                    db.session.add(new_booking)
                                    db.session.commit()
                                    
                                    flash(f'Berhasil membuat booking untuk {len(booking_dates)} hari! Silakan tunggu persetujuan admin.', 'success')
                                    return redirect(url_for('member_bookings'))
                                    
                                except Exception as e:
                                    db.session.rollback()
                                    error = f'Terjadi kesalahan saat menyimpan booking: {str(e)}'
                            
            except ValueError as e:
                error = 'Format tanggal tidak valid.'
            except Exception as e:
                error = f'Terjadi kesalahan: {str(e)}'
    
    # Ambil booking yang sudah ada untuk ditampilkan (2 hari ke depan + 21 hari)
    today = datetime.now().date()
    start_date = today + timedelta(days=2)
    end_date = start_date + timedelta(days=21)
    
    # Get all bookings and extract individual dates
    existing_bookings = Booking.query.filter(
        Booking.room_id == room_id,
        Booking.status.in_([BookingStatus.PENDING, BookingStatus.APPROVED])
    ).all()
    
    # Extract booked dates from existing bookings
    booked_dates = []
    booking_details = []
    
    for booking in existing_bookings:
        dates_list = booking.get_booking_dates_list()
        for date_str in dates_list:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            if start_date <= date_obj <= end_date:
                booked_dates.append(date_obj)
                booking_details.append({
                    'date': date_obj,
                    'status': booking.status,
                    'booking_id': booking.id
                })
    
    return render_template('member/book_room.html', 
                         room=room, 
                         user=user, 
                         error=error, 
                         booked_dates=booked_dates,
                         booking_details=booking_details)



@app.route('/member/bookings')
@member_required
def member_bookings():
    user = User.query.get(session['user_id'])
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    # Filter berdasarkan status jika ada
    status_filter = request.args.get('status', '')
    
    query = Booking.query.filter_by(user_id=user.id)
    
    if status_filter and status_filter in ['pending', 'approved', 'rejected', 'completed']:
        query = query.filter_by(status=BookingStatus(status_filter))
    
    bookings = query.order_by(Booking.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # Statistik booking user
    stats = {
        'total': Booking.query.filter_by(user_id=user.id).count(),
        'pending': Booking.query.filter_by(user_id=user.id, status=BookingStatus.PENDING).count(),
        'approved': Booking.query.filter_by(user_id=user.id, status=BookingStatus.APPROVED).count(),
        'rejected': Booking.query.filter_by(user_id=user.id, status=BookingStatus.REJECTED).count(),
        'completed': Booking.query.filter_by(user_id=user.id, status=BookingStatus.COMPLETED).count(),
    }
    
    return render_template('member/bookings.html', 
                         bookings=bookings, 
                         user=user, 
                         stats=stats,
                         current_status=status_filter)

@app.route('/member/booking/cancel/<int:booking_id>', methods=['POST'])
@member_required
def member_cancel_booking(booking_id):
    user = User.query.get(session['user_id'])
    booking = Booking.query.filter_by(id=booking_id, user_id=user.id).first_or_404()
    
    # Hanya booking dengan status PENDING yang bisa dibatalkan
    if booking.status != BookingStatus.PENDING:
        flash('Hanya booking dengan status pending yang dapat dibatalkan.', 'error')
    else:
        booking.status = BookingStatus.REJECTED
        db.session.commit()
        flash('Booking berhasil dibatalkan.', 'success')
    
    return redirect(url_for('member_bookings'))

@app.route('/member/booking/detail/<int:booking_id>')
@member_required
def member_booking_detail(booking_id):
    user = User.query.get(session['user_id'])
    booking = Booking.query.filter_by(id=booking_id, user_id=user.id).first_or_404()
    
    # Cek apakah ada sanksi terkait booking ini
    sanctions = Sanction.query.filter_by(booking_id=booking_id).all()
    
    return render_template('member/booking_detail.html', 
                         booking=booking, 
                         user=user, 
                         sanctions=sanctions)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')


        # Validasi
        if not username or not email or not password or not confirm_password:
            error = 'Semua field wajib diisi.'
        elif password != confirm_password:
            error = 'Password dan konfirmasi tidak cocok.'
        elif len(password) < 6:
            error = 'Password minimal 6 karakter.'
        elif User.query.filter_by(username=username).first():
            error = 'Username sudah digunakan.'
        elif User.query.filter_by(email=email).first():
            error = 'Email sudah digunakan.'
        elif not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            error = 'Format email tidak valid.'
        else:
            member_role = Role.query.filter_by(name='member').first()
            if not member_role:
                error = 'Role member tidak ditemukan. Hubungi admin.'
            else:
                new_user = User(
                    username=username,
                    email=email,
                    password_hash=generate_password_hash(password),
                    role_id=member_role.id
                )
                db.session.add(new_user)
                db.session.commit()
                return redirect(url_for('login'))

    return render_template('register.html', error=error)

#######################################################################################################
# Admin

# Decorator untuk proteksi admin
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        
        user = User.query.get(session['user_id'])
        if not user or user.role_id != 1:  # role_id 1 = admin
            flash('Akses ditolak. Hanya admin yang dapat mengakses halaman ini.', 'error')
            return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    return decorated_function

# Ganti route admin_dashboard yang sudah ada dengan yang baru
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    # Statistik dashboard
    total_users = User.query.count()
    total_rooms = Room.query.count()
    total_bookings = Booking.query.count()
    pending_bookings = Booking.query.filter_by(status=BookingStatus.PENDING).count()
    
    stats = {
        'total_users': total_users,
        'total_rooms': total_rooms,
        'total_bookings': total_bookings,
        'pending_bookings': pending_bookings
    }
    
    return render_template('admin/dashboard.html', stats=stats)

# Route untuk menampilkan daftar user
@app.route('/admin/users')
@admin_required
def admin_users():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    users = User.query.join(Role).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('admin/users.html', users=users)

# Route untuk menambah user baru
@app.route('/admin/users/add', methods=['GET', 'POST'])
@admin_required
def admin_add_user():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role_id = request.form.get('role_id', type=int)
        is_active = request.form.get('is_active') == 'on'

        # Validasi
        if not username or not email or not password or not role_id:
            error = 'Semua field wajib diisi.'
        elif len(password) < 6:
            error = 'Password minimal 6 karakter.'
        elif User.query.filter_by(username=username).first():
            error = 'Username sudah digunakan.'
        elif User.query.filter_by(email=email).first():
            error = 'Email sudah digunakan.'
        elif not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            error = 'Format email tidak valid.'
        elif not Role.query.get(role_id):
            error = 'Role tidak valid.'
        else:
            new_user = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                role_id=role_id,
                is_active=is_active
            )
            db.session.add(new_user)
            db.session.commit()
            flash('User berhasil ditambahkan!', 'success')
            return redirect(url_for('admin_users'))

    roles = Role.query.filter_by(is_active=True).all()
    return render_template('admin/add_user.html', error=error, roles=roles)

# Route untuk edit user
@app.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_user(user_id):
    user = User.query.get_or_404(user_id)
    error = None
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        role_id = request.form.get('role_id', type=int)
        is_active = request.form.get('is_active') == 'on'

        # Validasi
        if not username or not email or not role_id:
            error = 'Username, email, dan role wajib diisi.'
        elif User.query.filter(User.username == username, User.id != user_id).first():
            error = 'Username sudah digunakan oleh user lain.'
        elif User.query.filter(User.email == email, User.id != user_id).first():
            error = 'Email sudah digunakan oleh user lain.'
        elif not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            error = 'Format email tidak valid.'
        elif not Role.query.get(role_id):
            error = 'Role tidak valid.'
        elif password and len(password) < 6:
            error = 'Password minimal 6 karakter.'
        else:
            user.username = username
            user.email = email
            user.role_id = role_id
            user.is_active = is_active
            
            # Update password jika diisi
            if password:
                user.password_hash = generate_password_hash(password)
            
            db.session.commit()
            flash('User berhasil diupdate!', 'success')
            return redirect(url_for('admin_users'))

    roles = Role.query.filter_by(is_active=True).all()
    return render_template('admin/edit_user.html', user=user, error=error, roles=roles)

# Route untuk delete user
@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # Cegah admin menghapus dirinya sendiri
    if user.id == session['user_id']:
        flash('Anda tidak dapat menghapus akun Anda sendiri!', 'error')
        return redirect(url_for('admin_users'))
    
    # Cek apakah user memiliki bookings aktif
    active_bookings = Booking.query.filter_by(user_id=user_id).filter(
        Booking.status.in_([BookingStatus.PENDING, BookingStatus.APPROVED])
    ).count()
    
    if active_bookings > 0:
        flash('User tidak dapat dihapus karena memiliki booking aktif!', 'error')
    else:
        # Soft delete: set is_active = False
        user.is_active = False
        db.session.commit()
        flash('User berhasil dihapus!', 'success')
    
    return redirect(url_for('admin_users'))

# Route untuk mengelola roles
@app.route('/admin/roles')
@admin_required
def admin_roles():
    roles = Role.query.all()
    return render_template('admin/roles.html', roles=roles)

# Route untuk menambah role baru
@app.route('/admin/roles/add', methods=['GET', 'POST'])
@admin_required
def admin_add_role():
    error = None
    if request.method == 'POST':
        name = request.form.get('name', '').strip().lower()
        description = request.form.get('description', '').strip()
        is_active = request.form.get('is_active') == 'on'

        if not name or not description:
            error = 'Nama dan deskripsi role wajib diisi.'
        elif Role.query.filter_by(name=name).first():
            error = 'Nama role sudah digunakan.'
        else:
            new_role = Role(
                name=name,
                description=description,
                is_active=is_active
            )
            db.session.add(new_role)
            db.session.commit()
            flash('Role berhasil ditambahkan!', 'success')
            return redirect(url_for('admin_roles'))

    return render_template('admin/add_role.html', error=error)

# Route untuk edit role
@app.route('/admin/roles/edit/<int:role_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_role(role_id):
    role = Role.query.get_or_404(role_id)
    error = None
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip().lower()
        description = request.form.get('description', '').strip()
        is_active = request.form.get('is_active') == 'on'

        if not name or not description:
            error = 'Nama dan deskripsi role wajib diisi.'
        elif Role.query.filter(Role.name == name, Role.id != role_id).first():
            error = 'Nama role sudah digunakan.'
        else:
            role.name = name
            role.description = description
            role.is_active = is_active
            db.session.commit()
            flash('Role berhasil diupdate!', 'success')
            return redirect(url_for('admin_roles'))

    return render_template('admin/edit_role.html', role=role, error=error)

# Route untuk toggle status role
@app.route('/admin/roles/toggle/<int:role_id>', methods=['POST'])
@admin_required
def admin_toggle_role(role_id):
    role = Role.query.get_or_404(role_id)
    
    # Cegah admin menonaktifkan role admin
    if role.name == 'admin':
        flash('Role admin tidak dapat dinonaktifkan!', 'error')
    else:
        role.is_active = not role.is_active
        db.session.commit()
        status = 'diaktifkan' if role.is_active else 'dinonaktifkan'
        flash(f'Role {role.name} berhasil {status}!', 'success')
    
    return redirect(url_for('admin_roles'))

# Route untuk melihat detail user
@app.route('/admin/users/detail/<int:user_id>')
@admin_required
def admin_user_detail(user_id):
    user = User.query.get_or_404(user_id)
    
    # Ambil statistik user
    total_bookings = Booking.query.filter_by(user_id=user_id).count()
    approved_bookings = Booking.query.filter_by(user_id=user_id, status=BookingStatus.APPROVED).count()
    pending_bookings = Booking.query.filter_by(user_id=user_id, status=BookingStatus.PENDING).count()
    active_sanctions = Sanction.query.filter_by(user_id=user_id, status=SanctionStatus.ACTIVE).count()
    
    user_stats = {
        'total_bookings': total_bookings,
        'approved_bookings': approved_bookings,
        'pending_bookings': pending_bookings,
        'active_sanctions': active_sanctions
    }
    
    # Ambil booking terbaru
    recent_bookings = Booking.query.filter_by(user_id=user_id).order_by(
        Booking.created_at.desc()
    ).limit(5).all()
    
    return render_template('admin/user_detail.html', 
                         user=user, 
                         user_stats=user_stats, 
                         recent_bookings=recent_bookings)


# Route untuk mengelola rooms
@app.route('/admin/rooms')
@admin_required
def admin_rooms():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    rooms = Room.query.paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('admin/rooms.html', rooms=rooms)

# Route untuk menambah room baru
@app.route('/admin/rooms/add', methods=['GET', 'POST'])
@admin_required
def admin_add_room():
    error = None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        capacity = request.form.get('capacity', type=int)
        is_active = request.form.get('is_active') == 'on'

        # Validasi
        if not name or not capacity:
            error = 'Nama ruangan dan kapasitas wajib diisi.'
        elif capacity <= 0:
            error = 'Kapasitas harus lebih dari 0.'
        elif Room.query.filter_by(name=name).first():
            error = 'Nama ruangan sudah digunakan.'
        else:
            new_room = Room(
                name=name,
                description=description,
                capacity=capacity,
                is_active=is_active
            )
            db.session.add(new_room)
            db.session.commit()
            flash('Ruangan berhasil ditambahkan!', 'success')
            return redirect(url_for('admin_rooms'))

    return render_template('admin/add_room.html', error=error)

# Route untuk edit room
@app.route('/admin/rooms/edit/<int:room_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_room(room_id):
    room = Room.query.get_or_404(room_id)
    error = None
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        capacity = request.form.get('capacity', type=int)
        is_active = request.form.get('is_active') == 'on'

        # Validasi
        if not name or not capacity:
            error = 'Nama ruangan dan kapasitas wajib diisi.'
        elif capacity <= 0:
            error = 'Kapasitas harus lebih dari 0.'
        elif Room.query.filter(Room.name == name, Room.id != room_id).first():
            error = 'Nama ruangan sudah digunakan oleh ruangan lain.'
        else:
            room.name = name
            room.description = description
            room.capacity = capacity
            room.is_active = is_active
            
            db.session.commit()
            flash('Ruangan berhasil diupdate!', 'success')
            return redirect(url_for('admin_rooms'))

    return render_template('admin/edit_room.html', room=room, error=error)

# Route untuk toggle status room
@app.route('/admin/rooms/toggle/<int:room_id>', methods=['POST'])
@admin_required
def admin_toggle_room(room_id):
    room = Room.query.get_or_404(room_id)
    
    # Cek apakah room memiliki booking aktif
    active_bookings = Booking.query.filter_by(room_id=room_id).filter(
        Booking.status.in_([BookingStatus.PENDING, BookingStatus.APPROVED])
    ).count()
    
    if not room.is_active and active_bookings > 0:
        flash('Ruangan tidak dapat dinonaktifkan karena memiliki booking aktif!', 'error')
    else:
        room.is_active = not room.is_active
        db.session.commit()
        status = 'diaktifkan' if room.is_active else 'dinonaktifkan'
        flash(f'Ruangan {room.name} berhasil {status}!', 'success')
    
    return redirect(url_for('admin_rooms'))

# Route untuk delete room
@app.route('/admin/rooms/delete/<int:room_id>', methods=['POST'])
@admin_required
def admin_delete_room(room_id):
    room = Room.query.get_or_404(room_id)
    
    # Cek apakah room memiliki bookings
    total_bookings = Booking.query.filter_by(room_id=room_id).count()
    
    if total_bookings > 0:
        flash('Ruangan tidak dapat dihapus karena memiliki riwayat booking!', 'error')
    else:
        db.session.delete(room)
        db.session.commit()
        flash('Ruangan berhasil dihapus!', 'success')
    
    return redirect(url_for('admin_rooms'))

# Route untuk melihat detail room
@app.route('/admin/rooms/detail/<int:room_id>')
@admin_required
def admin_room_detail(room_id):
    room = Room.query.get_or_404(room_id)
    
    # Ambil statistik room
    total_bookings = Booking.query.filter_by(room_id=room_id).count()
    approved_bookings = Booking.query.filter_by(room_id=room_id, status=BookingStatus.APPROVED).count()
    pending_bookings = Booking.query.filter_by(room_id=room_id, status=BookingStatus.PENDING).count()
    completed_bookings = Booking.query.filter_by(room_id=room_id, status=BookingStatus.COMPLETED).count()
    
    room_stats = {
        'total_bookings': total_bookings,
        'approved_bookings': approved_bookings,
        'pending_bookings': pending_bookings,
        'completed_bookings': completed_bookings
    }
    
    # Ambil booking terbaru
    recent_bookings = Booking.query.filter_by(room_id=room_id).order_by(
        Booking.created_at.desc()
    ).limit(10).all()
    
    return render_template('admin/room_detail.html', 
                         room=room, 
                         room_stats=room_stats, 
                         recent_bookings=recent_bookings)


# Untuk menjalankan langsung dengan python app.py
if __name__ == '__main__':
    app.run(debug=True)
