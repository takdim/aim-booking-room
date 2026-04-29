from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify, Blueprint
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask.cli import with_appcontext
from datetime import datetime, date, time, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from sqlalchemy import and_, or_

from models.models import db, User, Role, Room, Booking, Sanction, UserProfile, BookingStatus, SanctionStatus
from routes.member import member_bp
from routes.staff import staff_bp

import enum
import click
import re
import os
import json

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


app = Flask(__name__)
database_url = os.getenv('DATABASE_URL')
secret_key = os.getenv('SECRET_KEY')
if not database_url:
    raise RuntimeError('DATABASE_URL belum diset. Gunakan env var DATABASE_URL.')
if not secret_key:
    raise RuntimeError('SECRET_KEY belum diset. Gunakan env var SECRET_KEY.')

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SECRET_KEY'] = secret_key
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['UPLOAD_FOLDER'] = 'static/uploads/ktm'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)
migrate = Migrate(app, db)

app.register_blueprint(member_bp)
app.register_blueprint(staff_bp)

# ===== ERROR HANDLERS =====
@app.errorhandler(404)
def page_not_found(error):
    """Handle 404 errors"""
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    db.session.rollback()
    return render_template('errors/500.html'), 500

@app.errorhandler(403)
def forbidden(error):
    """Handle 403 errors"""
    return render_template('errors/403.html'), 403

# ===== CLI COMMANDS =====

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

@click.command('init-rooms')
@with_appcontext
def init_rooms():
    for i in range(1, 19):
        room_name = f"{i}"
        if not Room.query.filter_by(name=room_name).first():
            room = Room(
                name=room_name,
                description=f"Ruangan {i} untuk keperluan umum",
                capacity=1
            )
            db.session.add(room)
            print(f"✅ {room_name} berhasil ditambahkan.")
        else:
            print(f"ℹ️ {room_name} sudah ada, dilewati.")
    db.session.commit()
    print("✅ Semua ruangan berhasil diinisialisasi.")

app.cli.add_command(init_rooms)

@click.command('check-late-bookings')
@with_appcontext
def check_late_bookings():
    """Cek booking yang terlambat dan buat sanksi otomatis"""
    from routes.member import check_and_create_sanctions
    count = check_and_create_sanctions()
    if count == 0:
        print("ℹ️ Tidak ada booking baru yang terlambat.")
    else:
        print(f"✅ {count} sanksi baru telah dibuat untuk booking terlambat.")

app.cli.add_command(check_late_bookings)

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
    if 'user_id' in session and 'role_id' in session:
        # Jika sudah login, redirect ke dashboard sesuai role
        user = User.query.get(session['user_id'])
        if user:
            if user.role_id == 1:
                return redirect(url_for('admin_dashboard'))
            elif user.role_id == 2:
                return redirect(url_for('staff.staff_dashboard'))
            elif user.role_id == 3:
                return redirect(url_for('member.member_dashboard'))
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
                return redirect(url_for('staff.staff_dashboard'))
            elif user.role_id == 3:
                return redirect(url_for('member.member_dashboard'))
            else:
                error = 'Role tidak dikenal'

    return render_template('login.html', error=error)



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

# ===== ADMIN BOOKINGS MANAGEMENT =====

@app.route('/admin/bookings')
@admin_required
def admin_bookings():
    """Admin page untuk melihat dan mengelola semua bookings"""
    page = request.args.get('page', 1, type=int)
    per_page = 15
    status_filter = request.args.get('status', 'all')
    room_filter = request.args.get('room', 'all')
    
    query = Booking.query.join(User, Booking.user_id == User.id).join(Room)
    
    # Apply filters
    if status_filter != 'all':
        query = query.filter(Booking.status == BookingStatus(status_filter))
    
    if room_filter != 'all':
        query = query.filter(Room.id == int(room_filter))
    
    bookings = query.order_by(Booking.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # Get rooms untuk filter dropdown
    rooms = Room.query.filter_by(is_active=True).all()
    
    # Statistics
    stats = {
        'total': Booking.query.count(),
        'pending': Booking.query.filter_by(status=BookingStatus.PENDING).count(),
        'approved': Booking.query.filter_by(status=BookingStatus.APPROVED).count(),
        'checked_in': Booking.query.filter_by(status=BookingStatus.CHECKED_IN).count(),
        'completed': Booking.query.filter_by(status=BookingStatus.COMPLETED).count(),
        'rejected': Booking.query.filter_by(status=BookingStatus.REJECTED).count(),
    }
    
    return render_template('admin/bookings.html', 
                         bookings=bookings,
                         rooms=rooms,
                         current_filters={
                             'status': status_filter,
                             'room': room_filter
                         },
                         stats=stats)

@app.route('/admin/bookings/<int:booking_id>')
@admin_required
def admin_booking_detail(booking_id):
    """Detail view untuk booking tertentu"""
    booking = Booking.query.get_or_404(booking_id)
    user_profile = UserProfile.query.filter_by(user_id=booking.user_id).first()
    approver = None
    if booking.approved_by:
        approver = User.query.get(booking.approved_by)
    sanctions = Sanction.query.filter_by(booking_id=booking_id).all()
    
    return render_template('admin/booking_detail.html',
                         booking=booking,
                         user_profile=user_profile,
                         approver=approver,
                         sanctions=sanctions)

@app.route('/admin/bookings/<int:booking_id>/approve', methods=['POST'])
@admin_required
def admin_approve_booking(booking_id):
    """Admin approve booking"""
    booking = Booking.query.get_or_404(booking_id)
    
    if booking.status != BookingStatus.PENDING:
        flash('Hanya booking pending yang dapat disetujui', 'error')
    else:
        booking.status = BookingStatus.APPROVED
        booking.approved_by = session['user_id']
        booking.approved_at = datetime.utcnow()
        db.session.commit()
        flash('Booking berhasil disetujui', 'success')
    
    return redirect(url_for('admin_booking_detail', booking_id=booking_id))

@app.route('/admin/bookings/<int:booking_id>/reject', methods=['POST'])
@admin_required
def admin_reject_booking(booking_id):
    """Admin reject booking"""
    booking = Booking.query.get_or_404(booking_id)
    reason = request.form.get('reason', '')
    
    if booking.status != BookingStatus.PENDING:
        flash('Hanya booking pending yang dapat ditolak', 'error')
    else:
        booking.status = BookingStatus.REJECTED
        booking.approved_by = session['user_id']
        booking.approved_at = datetime.utcnow()
        if reason:
            booking.purpose = f"[REJECTED] {reason}\n\nOriginal: {booking.purpose}" if booking.purpose else f"[REJECTED] {reason}"
        db.session.commit()
        flash('Booking berhasil ditolak', 'success')
    
    return redirect(url_for('admin_booking_detail', booking_id=booking_id))

# ===== ADMIN SANCTIONS MANAGEMENT =====

@app.route('/admin/sanctions')
@admin_required
def admin_sanctions():
    """Admin page untuk melihat dan mengelola semua sanksi"""
    page = request.args.get('page', 1, type=int)
    per_page = 15
    status_filter = request.args.get('status', 'all')
    
    query = Sanction.query.join(User, Sanction.user_id == User.id).join(Booking, Sanction.booking_id == Booking.id)
    
    # Apply filter
    if status_filter != 'all':
        query = query.filter(Sanction.status == SanctionStatus(status_filter))
    
    sanctions = query.order_by(Sanction.issued_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # Statistics
    stats = {
        'total': Sanction.query.count(),
        'active': Sanction.query.filter_by(status=SanctionStatus.ACTIVE).count(),
        'paid': Sanction.query.filter_by(status=SanctionStatus.PAID).count(),
        'total_active_amount': db.session.query(db.func.sum(Sanction.amount)).filter_by(status=SanctionStatus.ACTIVE).scalar() or 0
    }
    
    return render_template('admin/sanctions.html',
                         sanctions=sanctions,
                         current_filters={'status': status_filter},
                         stats=stats)

@app.route('/admin/sanctions/<int:sanction_id>/mark-paid', methods=['POST'])
@admin_required
def admin_mark_sanction_paid(sanction_id):
    """Mark sanction as paid"""
    sanction = Sanction.query.get_or_404(sanction_id)
    
    if sanction.status == SanctionStatus.PAID:
        flash('Sanksi ini sudah ditandai sebagai lunas', 'info')
    else:
        sanction.status = SanctionStatus.PAID
        sanction.paid_at = datetime.utcnow()
        db.session.commit()
        flash('Sanksi berhasil ditandai sebagai lunas', 'success')
    
    return redirect(url_for('admin_sanctions'))

@app.route('/admin/check-late-bookings', methods=['POST'])
@admin_required
def admin_check_late_bookings():
    """Trigger check and create sanctions for late bookings"""
    from routes.member import check_and_create_sanctions
    count = check_and_create_sanctions()
    
    if count == 0:
        flash('Tidak ada booking baru yang terlambat.', 'info')
    else:
        flash(f'{count} sanksi baru telah dibuat untuk booking terlambat.', 'success')
    
    return redirect(url_for('admin_sanctions'))

# ===== ANALYTICS DASHBOARD =====

@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    """Admin analytics dashboard"""
    from datetime import timedelta
    
    # Date range (default: last 30 days)
    end_date = date.today()
    start_date = end_date - timedelta(days=30)
    
    # Overall Statistics
    total_users = User.query.count()
    total_rooms = Room.query.count()
    total_bookings = Booking.query.count()
    total_completed = Booking.query.filter_by(status=BookingStatus.COMPLETED).count()
    total_sanctions = Sanction.query.count()
    total_sanctions_amount = db.session.query(db.func.sum(Sanction.amount)).scalar() or 0
    
    # Bookings by status
    status_stats = {
        'pending': Booking.query.filter_by(status=BookingStatus.PENDING).count(),
        'approved': Booking.query.filter_by(status=BookingStatus.APPROVED).count(),
        'checked_in': Booking.query.filter_by(status=BookingStatus.CHECKED_IN).count(),
        'completed': total_completed,
        'rejected': Booking.query.filter_by(status=BookingStatus.REJECTED).count(),
    }
    
    # Top 5 rooms by booking count
    top_rooms = db.session.query(
        Room.name,
        db.func.count(Booking.id).label('booking_count')
    ).join(Booking).group_by(Room.id).order_by(
        db.func.count(Booking.id).desc()
    ).limit(5).all()
    
    # Booking trend (last 30 days)
    booking_trend = db.session.query(
        Booking.booking_date,
        db.func.count(Booking.id).label('count')
    ).filter(
        Booking.booking_date.between(start_date, end_date)
    ).group_by(Booking.booking_date).order_by(
        Booking.booking_date
    ).all()
    
    # Top members by booking count
    top_members = db.session.query(
        User.username,
        db.func.count(Booking.id).label('booking_count')
    ).join(Booking, Booking.user_id == User.id).group_by(User.id).order_by(
        db.func.count(Booking.id).desc()
    ).limit(5).all()
    
    # Active vs Completed bookings ratio
    late_checkouts = Booking.query.filter_by(is_late=True).count()
    
    # Prepare data for charts
    chart_data = {
        'status_labels': list(status_stats.keys()),
        'status_values': list(status_stats.values()),
        'room_labels': [room[0] for room in top_rooms],
        'room_values': [room[1] for room in top_rooms],
        'member_labels': [member[0] for member in top_members],
        'member_values': [member[1] for member in top_members],
        'trend_labels': [str(booking[0]) for booking in booking_trend],
        'trend_values': [booking[1] for booking in booking_trend],
    }
    
    stats = {
        'total_users': total_users,
        'total_rooms': total_rooms,
        'total_bookings': total_bookings,
        'total_completed': total_completed,
        'completion_rate': round((total_completed / total_bookings * 100) if total_bookings > 0 else 0, 1),
        'total_sanctions': total_sanctions,
        'total_sanctions_amount': int(total_sanctions_amount),
        'late_checkouts': late_checkouts,
        'status_stats': status_stats,
    }
    
    return render_template('admin/analytics.html',
                         stats=stats,
                         chart_data=chart_data,
                         top_rooms=top_rooms,
                         top_members=top_members)

@app.route('/admin/calendar')
@admin_required
def admin_calendar():
    """Booking calendar view"""
    room_id = request.args.get('room', 'all')
    month = request.args.get('month', date.today().month, type=int)
    year = request.args.get('year', date.today().year, type=int)
    
    rooms = Room.query.filter_by(is_active=True).all()
    
    # Get calendar data
    import calendar
    cal = calendar.monthcalendar(year, month)
    
    # Get all bookings for the month
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)
    
    query = Booking.query.filter(
        Booking.booking_date.between(month_start, month_end),
        Booking.status.in_([BookingStatus.APPROVED, BookingStatus.CHECKED_IN, BookingStatus.COMPLETED])
    )
    
    if room_id != 'all':
        query = query.filter(Booking.room_id == int(room_id))
    
    bookings = query.all()
    
    # Prepare booking data by date
    booking_by_date = {}
    for booking in bookings:
        date_str = booking.booking_date.strftime('%Y-%m-%d')
        if date_str not in booking_by_date:
            booking_by_date[date_str] = []
        booking_by_date[date_str].append({
            'room': booking.room.name,
            'user': booking.user.username,
            'status': booking.status.value,
            'time': f"{booking.start_time.strftime('%H:%M')}-{booking.end_time.strftime('%H:%M')}"
        })
    
    # Prepare calendar with booking info
    calendar_data = []
    for week in cal:
        week_data = []
        for day in week:
            if day == 0:
                week_data.append(None)
            else:
                day_date = date(year, month, day)
                date_str = day_date.strftime('%Y-%m-%d')
                day_bookings = booking_by_date.get(date_str, [])
                week_data.append({
                    'day': day,
                    'date': date_str,
                    'booking_count': len(day_bookings),
                    'bookings': day_bookings,
                    'is_today': day_date == date.today()
                })
        calendar_data.append(week_data)
    
    # Navigation
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    
    return render_template('admin/calendar.html',
                         calendar_data=calendar_data,
                         rooms=rooms,
                         current_room=room_id,
                         current_month=month,
                         current_year=year,
                         month_name=calendar.month_name[month],
                         prev_link=f"?room={room_id}&month={prev_month}&year={prev_year}",
                         next_link=f"?room={room_id}&month={next_month}&year={next_year}")


# Untuk menjalankan langsung dengan python app.py
if __name__ == '__main__':
    app.run(debug=True)
