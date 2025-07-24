from flask import Blueprint, render_template, redirect, url_for, session, flash, request, current_app, send_file, jsonify, flash
from functools import wraps
from datetime import datetime, date, time, timedelta
from werkzeug.utils import secure_filename

from models.models import db, User, Booking, Room, UserProfile, BookingStatus, Sanction, SanctionStatus

import os

member_bp = Blueprint('member', __name__, url_prefix='/member')

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'pdf'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
@member_bp.route('/member/dashboard')
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
    
    return render_template("member/dashboard2.html", 
                         user=user,
                         rooms=rooms, 
                         member_stats=member_stats,
                         recent_bookings=recent_bookings)

@member_bp.route('/member/profile', methods=['GET', 'POST'])
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
                        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], ktm_filename)
                        ktm_file.save(file_path)
                
                if not error:
                    if profile:
                        # Update existing profile
                        # Hapus file lama jika ada file baru
                        if ktm_filename and profile.ktm_filename:
                            old_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], profile.ktm_filename)
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
                    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], ktm_filename)
                    if os.path.exists(file_path):
                        os.remove(file_path)
    
    return render_template('member/profile.html', 
                         user=user, 
                         profile=profile, 
                         prodi_options=prodi_options,
                         error=error, 
                         success=success)

@member_bp.route('/member/profile/download-ktm')
@member_required
def member_download_ktm():
    user = User.query.get(session['user_id'])
    profile = UserProfile.query.filter_by(user_id=user.id).first()
    
    if not profile or not profile.ktm_filename:
        flash('File KTM tidak ditemukan.', 'error')
        return redirect(url_for('member_profile'))
    
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], profile.ktm_filename)
    
    if not os.path.exists(file_path):
        flash('File KTM tidak ditemukan di server.', 'error')
        return redirect(url_for('member_profile'))
    
    return send_file(file_path, as_attachment=True, download_name=f"KTM_{profile.full_name}.pdf")

@member_bp.route('/member/profile/delete-ktm', methods=['POST'])
@member_required
def member_delete_ktm():
    user = User.query.get(session['user_id'])
    profile = UserProfile.query.filter_by(user_id=user.id).first()
    
    if not profile or not profile.ktm_filename:
        flash('File KTM tidak ditemukan.', 'error')
        return redirect(url_for('member_profile'))
    
    # Hapus file dari server
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], profile.ktm_filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    
    # Hapus referensi dari database
    profile.ktm_filename = None
    profile.updated_at = datetime.utcnow()
    db.session.commit()
    
    flash('File KTM berhasil dihapus.', 'success')
    return redirect(url_for('member_profile'))


# Route yang dimodifikasi
@member_bp.route('/member/book/<int:room_id>', methods=['GET', 'POST'])
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
                                    return redirect(url_for('member.member_bookings'))
                                    
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



@member_bp.route('/member/bookings')
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

@member_bp.route('/member/booking/cancel/<int:booking_id>', methods=['POST'])
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
    
    return redirect(url_for('member.member_bookings'))

@member_bp.route('/member/booking/detail/<int:booking_id>')
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

def check_and_create_sanctions():
    """
    Fungsi untuk mengecek booking yang terlambat dan membuat sanksi otomatis
    """
    current_time = datetime.now()
    
    # Cari booking yang approved tapi belum checkout dan sudah melewati waktu
    overdue_bookings = Booking.query.filter(
        Booking.status == BookingStatus.APPROVED,
        Booking.checkout_time.is_(None),  # Belum checkout
        Booking.is_late == False  # Belum ditandai terlambat
    ).all()
    
    sanctions_created = 0
    
    for booking in overdue_bookings:
        # Cek apakah sudah melewati jatuh tempo
        booking_datetime = datetime.combine(booking.booking_date, booking.end_time)
        
        # Jika multi-day booking, gunakan tanggal terakhir
        if booking.booking_dates:
            dates_list = booking.get_booking_dates_list()
            last_date = datetime.strptime(dates_list[-1], '%Y-%m-%d').date()
            booking_datetime = datetime.combine(last_date, booking.end_time)
        
        # Grace period 15 menit sebelum dianggap terlambat
        grace_period = timedelta(minutes=15)
        deadline = booking_datetime + grace_period
        
        if current_time > deadline:
            # Tandai booking sebagai terlambat
            booking.is_late = True
            
            # Cek apakah sudah ada sanksi untuk booking ini
            existing_sanction = Sanction.query.filter_by(booking_id=booking.id).first()
            
            if not existing_sanction:
                # Buat sanksi baru
                sanction = Sanction(
                    user_id=booking.user_id,
                    booking_id=booking.id,
                    amount=50000,  # Denda Rp 50.000
                    reason=f"Terlambat checkout untuk booking ruang {booking.room.name} pada {booking.booking_date}",
                    issued_by=1,  # ID admin/system (sesuaikan dengan ID admin Anda)
                    status=SanctionStatus.ACTIVE
                )
                
                db.session.add(sanction)
                sanctions_created += 1
    
    if sanctions_created > 0:
        db.session.commit()
        print(f"✅ {sanctions_created} sanksi baru dibuat untuk booking terlambat")
    
    return sanctions_created

@member_bp.route('/member/sanctions')
@member_required
def member_sanctions():
    user = User.query.get(session['user_id'])
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    # Filter berdasarkan status
    status_filter = request.args.get('status', '')
    
    query = Sanction.query.filter_by(user_id=user.id)
    
    if status_filter and status_filter in ['active', 'paid']:
        query = query.filter_by(status=SanctionStatus(status_filter.upper()))
    
    sanctions = query.order_by(Sanction.issued_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # Statistik sanksi user
    stats = {
        'total': Sanction.query.filter_by(user_id=user.id).count(),
        'active': Sanction.query.filter_by(user_id=user.id, status=SanctionStatus.ACTIVE).count(),
        'paid': Sanction.query.filter_by(user_id=user.id, status=SanctionStatus.PAID).count(),
        'total_amount': db.session.query(db.func.sum(Sanction.amount)).filter_by(
            user_id=user.id, status=SanctionStatus.ACTIVE
        ).scalar() or 0
    }
    
    return render_template('member/sanctions.html', 
                         sanctions=sanctions, 
                         user=user, 
                         stats=stats,
                         current_status=status_filter)

@member_bp.route('/member/bookings/<int:booking_id>/checkout', methods=['POST'])
@member_required
def checkout_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    
    # Pastikan ini booking milik user yang login
    if booking.user_id != session['user_id']:
        flash('Akses ditolak', 'error')
        return redirect(url_for('member.member_bookings'))
    
    # Pastikan booking dalam status approved
    if booking.status != BookingStatus.APPROVED:
        flash('Booking tidak dapat di-checkout', 'error')
        return redirect(url_for('member.member_bookings'))
    
    # Checkout
    booking.checkout_time = datetime.utcnow()
    booking.status = BookingStatus.COMPLETED
    
    db.session.commit()
    
    flash('Checkout berhasil!', 'success')
    return redirect(url_for('member.member_bookings'))

def get_user_active_sanctions_total(user_id):
    """Mendapatkan total sanksi aktif untuk user"""
    total = db.session.query(db.func.sum(Sanction.amount)).filter_by(
        user_id=user_id, 
        status=SanctionStatus.ACTIVE
    ).scalar()
    return total or 0
