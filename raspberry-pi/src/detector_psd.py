'''
Author: Emily Barosin, Integral Consulting Inc.
Date: February 2026

Description
------------
Detector functions for MPA management system.
'''

import numpy as np
from scipy import signal
import scipy.io.wavfile as wavfile
from scipy import signal
import warnings


def read_file(filename, decimation_factor=4):

    warnings.filterwarnings('ignore', category=wavfile.WavFileWarning)

    fs, data = wavfile.read(filename)

    # Decimate data by a factor of 4
    subdata = signal.decimate(data, decimation_factor)
    fs = fs // decimation_factor

    t = np.arange(0, len(subdata))/fs

    return fs, subdata, t

def calc_psds(filename):
    fs, data, t = read_file(filename)

    # Calculate number of 1-second chunks
    chunk_size = fs  # 1 second of samples
    num_chunks = len(data) // chunk_size

    # Store PSD for each chunk
    psd_list = []
    frequencies = None

    # Process each 1-second chunk
    for i in range(num_chunks):
        start_idx = i * chunk_size
        end_idx = start_idx + chunk_size
        chunk = data[start_idx:end_idx]

        # Compute PSD
        frequencies, psd = signal.welch(chunk, fs=fs, nfft = 512)

        # Convert to dB
        psd_db = 10 * np.log10(psd + 1e-10)
        psd_list.append(psd_db)

    # Stack all PSDs into a 2D array (n_chunks x n_frequencies)
    psd_matrix = np.array(psd_list)

    return psd_matrix, frequencies

def read_fft_file(fft_file):
    """
    Read text file with spectral data and return frequency, timestamps, and PSD arrays.

    The file format has:
    - Line 32: Header with frequencies starting after 'Data Points' column
    - Line 33+: Data rows with timestamp and spectral values
    - Each timestamp is 1 second apart

    Parameters:
    -----------
    fft_file : str
        Path to the text file (e.g., SJF7099_20260112_222000.txt)

    Returns:
    --------
    frequencies : np.ndarray
        Frequency vector (Hz) - shape (n_freqs,)
    timestamps : np.ndarray
        Timestamp strings for each spectrum - shape (n_times,)
    psd_db : np.ndarray
        Power spectral density in dB - shape (n_times, n_freqs)
    """
    # Read the entire file
    with open(fft_file, 'r') as f:
        lines = f.readlines()

    # Line 33 (index 32) contains the header with frequencies
    header_line = lines[31].strip().split('\t')

    # Frequencies start after column 5 (Time, Comment, Temperature, Humidity, Sequence #, Data Points)
    frequencies = np.array([float(freq) for freq in header_line[6:]])

    # Data starts at line 33 (index 32)
    # Preallocate arrays for performance
    data_lines = [line for line in lines[32:] if line.strip()]
    n_times = len(data_lines)
    n_freqs = len(frequencies)

    timestamps = np.empty(n_times, dtype=object)
    psd_db = np.empty((n_times, n_freqs), dtype=float)

    for i, line in enumerate(data_lines):
        parts = line.strip().split('\t')
        timestamps[i] = parts[0]  # Time column
        # Spectral values start at column 6
        psd_db[i, :] = [float(val) for val in parts[6:]]

    # Replace infinity values with NaN to avoid downstream errors
    psd_db[np.isinf(psd_db)] = np.nan

    return frequencies, timestamps, psd_db



def detect_tonal_peaks(psd_db, frequencies,
                       threshold_db=.5,
                       df_search_hz=15,
                       f_min=55, f_max=1000,
                       min_peak_separation_hz=2,
                       min_consecutive_detections=5,
                       sliding_windows=((10, 5), (21, 7)),
                       verbose=False):
    """
    Detect tonal peaks in PSD spectrum using adaptive thresholding.

    This function implements an adaptive peak detection algorithm where each peak
    must exceed the local background (within ±df_search) by a specified threshold.
    This is more robust than global threshold-based detection as it accounts for
    varying background noise levels across the frequency spectrum.

    Algorithm:
    1. For each timestamp in the PSD matrix:
       a. Find all local maxima in the PSD spectrum
       b. For each maximum at frequency f_peak:
          - Define search windows: [f_peak - df_search, f_peak] and [f_peak, f_peak + df_search]
          - Find the maximum PSD value in each window (excluding f_peak itself)
          - Peak is valid if: PSD(f_peak) > max(left_max, right_max) + threshold_db
       c. Filter peaks by frequency range and minimum separation
       d. Mark timestamp as having multiple peaks if >= 2 valid peaks detected
    2. Return True if ANY condition is met:
       a. (Consecutive) >= min_consecutive_detections consecutive timestamps have multiple peaks
       b. (Sliding window) Any window of size W has >= H timestamps with multiple peaks,
          for each (W, H) pair in sliding_windows

    Parameters:
    -----------
    psd_db : np.ndarray
        Power spectral density in dB (2D array: n_timestamps x n_frequencies)
    frequencies : np.ndarray
        Frequency vector corresponding to psd_db columns (Hz)
    threshold_db : float, optional
        dB threshold above local background for peak detection (default: 0.5)
    df_search_hz : float, optional
        Half-bandwidth (Hz) around each peak to examine for background level (default: 15)
    f_min : float, optional
        Minimum frequency (Hz) to search for peaks (default: 55)
    f_max : float, optional
        Maximum frequency (Hz) to search for peaks (default: 1000)
    min_peak_separation_hz : float, optional
        Minimum frequency separation (Hz) between detected peaks (default: 2)
    min_consecutive_detections : int, optional
        Minimum number of consecutive timestamps with multiple peaks required (default: 5)
    sliding_windows : sequence of (window_size, min_hits) pairs, optional
        Each pair defines one sliding-window OR condition.
        Default: ((10, 5), (21, 7)) — 5 hits in 10 s OR 7 hits in 21 s

    Returns:
    --------
    bool
        True if either the consecutive OR sliding-window condition is met, False otherwise
    """
    # Handle both 1D and 2D input for backward compatibility
    if psd_db.ndim == 1:
        psd_db = psd_db.reshape(1, -1)

    n_timestamps, n_freqs = psd_db.shape

    if verbose:
        print(f"\n=== TONAL PEAKS DETECTION DIAGNOSTICS ===")
        print(f"PSD shape: {psd_db.shape} (timestamps x frequencies)")
        print(f"Frequency range: {frequencies[0]:.1f} - {frequencies[-1]:.1f} Hz")
        print(f"Parameters: threshold_db={threshold_db}, df_search_hz={df_search_hz}")
        print(f"            f_min={f_min}, f_max={f_max}, min_peak_sep={min_peak_separation_hz}")
        print(f"            min_consecutive={min_consecutive_detections}")
        for w_size, w_hits in sliding_windows:
            print(f"            sliding_window={w_hits}/{w_size}s")

    # Filter to frequency range of interest
    freq_mask = (frequencies >= f_min) & (frequencies <= f_max)
    freqs_filtered = frequencies[freq_mask]

    if verbose:
        print(f"Frequency range filtered: {f_min}-{f_max} Hz -> {len(freqs_filtered)} frequencies")

    if len(freqs_filtered) == 0:
        if verbose:
            print("ERROR: No frequencies in range!")
        return False

    # Calculate frequency resolution
    df = np.mean(np.diff(freqs_filtered))

    if df <= 0 or not np.isfinite(df):
        return False

    # Find all local maxima parameters
    min_distance_samples = max(1, int(min_peak_separation_hz / df))
    search_window_samples = int(df_search_hz / df)

    if verbose:
        print(f"Frequency resolution: {df:.2f} Hz")
        print(f"Min distance (samples): {min_distance_samples}, Search window (samples): {search_window_samples}")

    # Track which timestamps have multiple peaks
    has_multiple_peaks = np.zeros(n_timestamps, dtype=bool)

    # Process each timestamp
    for t_idx in range(n_timestamps):
        psd_filtered = psd_db[t_idx, freq_mask]

        # Skip timestamps with NaN values (from Inf in source data)
        if np.any(np.isnan(psd_filtered)):
            continue

        # Find peaks with minimal constraints (just need local maxima)
        peak_indices_local, properties = signal.find_peaks(
            psd_filtered,
            distance=min_distance_samples
        )

        if verbose:
            print(f"\nTimestamp {t_idx}: found {len(peak_indices_local)} local maxima")

        if len(peak_indices_local) == 0:
            continue

        # Apply adaptive thresholding
        valid_peaks = []

        for peak_num, idx in enumerate(peak_indices_local):
            peak_amp = psd_filtered[idx]
            peak_freq = freqs_filtered[idx]

            # Define left and right search windows
            left_start = max(0, idx - search_window_samples)
            left_end = idx
            right_start = idx + 1
            right_end = min(len(psd_filtered), idx + search_window_samples + 1)

            # Find maximum in left window (excluding peak itself)
            if left_end > left_start:
                left_max = np.max(psd_filtered[left_start:left_end])
            else:
                left_max = -np.inf

            # Find maximum in right window (excluding peak itself)
            if right_end > right_start:
                right_max = np.max(psd_filtered[right_start:right_end])
            else:
                right_max = -np.inf

            # Local background is the higher of the two surrounding maxima
            local_background = max(left_max, right_max)

            # Calculate prominence above local background
            prominence = peak_amp - local_background

            if verbose:
                print(f"  Peak {peak_num}: freq={peak_freq:.1f} Hz, amp={peak_amp:.2f} dB, " +
                      f"local_bg={local_background:.2f} dB, prominence={prominence:.2f} dB, " +
                      f"passes={prominence >= threshold_db}")

            # Check if peak exceeds threshold
            if prominence >= threshold_db:
                valid_peaks.append(idx)

        if verbose:
            print(f"  -> {len(valid_peaks)} valid peaks (threshold={threshold_db} dB)")

        # Mark if this timestamp has multiple valid peaks
        if len(valid_peaks) >= 2:
            has_multiple_peaks[t_idx] = True
            if verbose:
                print(f"  -> MULTIPLE PEAKS DETECTED")
        elif verbose:
            print(f"  -> Not enough valid peaks (need >= 2)")

    # Check for consecutive detections
    max_consecutive = 0
    current_consecutive = 0

    for t_idx, has_peaks in enumerate(has_multiple_peaks):
        if has_peaks:
            current_consecutive += 1
            max_consecutive = max(max_consecutive, current_consecutive)
        else:
            current_consecutive = 0

    consecutive_positive = max_consecutive >= min_consecutive_detections

    # Check sliding window conditions (OR across all pairs)
    sliding_positive = False
    for w_size, w_hits in sliding_windows:
        if n_timestamps >= w_size:
            for start in range(n_timestamps - w_size + 1):
                if np.sum(has_multiple_peaks[start:start + w_size]) >= w_hits:
                    sliding_positive = True
                    break
        if sliding_positive:
            break

    result = consecutive_positive or sliding_positive

    if verbose:
        print(f"\nConsecutive detection check:")
        print(f"  Timestamps with multiple peaks: {np.where(has_multiple_peaks)[0].tolist()}")
        print(f"  Max consecutive detections: {max_consecutive}")
        print(f"  Min required: {min_consecutive_detections}")
        print(f"  RESULT: {'POSITIVE (True)' if consecutive_positive else 'NEGATIVE (False)'}")
        for w_size, w_hits in sliding_windows:
            print(f"\nSliding window check ({w_hits}/{w_size}s):")
        print(f"  RESULT: {'POSITIVE (True)' if sliding_positive else 'NEGATIVE (False)'}")
        print(f"\nFinal RESULT (consecutive OR sliding): {'POSITIVE (True)' if result else 'NEGATIVE (False)'}")
        print(f"==========================================\n")

    return result


def get_chunk_peaks(psd_db, frequencies,
                    threshold_db=.5,
                    df_search_hz=15,
                    f_min=55, f_max=1000,
                    min_peak_separation_hz=2,
                    min_consecutive_detections=5,
                    sliding_windows=((10, 5), (21, 7))):
    """
    Same algorithm as detect_tonal_peaks, but returns per-chunk peak frequencies
    and a mask indicating which chunks qualify under either detection condition.

    Returns:
        chunk_peaks : list of lists
            chunk_peaks[i] is the list of valid peak frequencies (Hz) in chunk i
            (only populated when >= 2 valid peaks are present).
        detection_mask : np.ndarray of bool
            True for chunks that are part of a run of >= min_consecutive_detections
            consecutive chunks with multiple valid peaks, OR within any sliding window
            that satisfies one of the (size, min_hits) pairs in sliding_windows.
    """
    if psd_db.ndim == 1:
        psd_db = psd_db.reshape(1, -1)

    n_timestamps, _ = psd_db.shape

    freq_mask = (frequencies >= f_min) & (frequencies <= f_max)
    freqs_filtered = frequencies[freq_mask]

    empty = [[] for _ in range(n_timestamps)]
    if len(freqs_filtered) == 0:
        return empty, np.zeros(n_timestamps, dtype=bool)

    df = np.mean(np.diff(freqs_filtered))
    if df <= 0 or not np.isfinite(df):
        return empty, np.zeros(n_timestamps, dtype=bool)

    min_distance_samples  = max(1, int(min_peak_separation_hz / df))
    search_window_samples = int(df_search_hz / df)

    chunk_peaks        = []
    has_multiple_peaks = np.zeros(n_timestamps, dtype=bool)

    for t_idx in range(n_timestamps):
        psd_filtered = psd_db[t_idx, freq_mask]

        if np.any(np.isnan(psd_filtered)):
            chunk_peaks.append([])
            continue

        peak_indices, _ = signal.find_peaks(psd_filtered, distance=min_distance_samples)

        valid_freqs = []
        for idx in peak_indices:
            peak_amp = psd_filtered[idx]

            left_start = max(0, idx - search_window_samples)
            right_end  = min(len(psd_filtered), idx + search_window_samples + 1)

            left_max  = np.max(psd_filtered[left_start:idx])       if idx > left_start   else -np.inf
            right_max = np.max(psd_filtered[idx + 1:right_end])     if right_end > idx + 1 else -np.inf

            if peak_amp - max(left_max, right_max) >= threshold_db:
                valid_freqs.append(freqs_filtered[idx])

        if len(valid_freqs) >= 2:
            has_multiple_peaks[t_idx] = True
            chunk_peaks.append(valid_freqs)
        else:
            chunk_peaks.append([])

    # Mark chunks that are part of a consecutive run long enough to trigger detection
    detection_mask = np.zeros(n_timestamps, dtype=bool)
    i = 0
    while i < n_timestamps:
        if has_multiple_peaks[i]:
            j = i + 1
            while j < n_timestamps and has_multiple_peaks[j]:
                j += 1
            if (j - i) >= min_consecutive_detections:
                detection_mask[i:j] = True
            i = j
        else:
            i += 1

    # OR condition: mark chunks in any sliding window that meets any (size, hits) pair
    for w_size, w_hits in sliding_windows:
        if n_timestamps >= w_size:
            for start in range(n_timestamps - w_size + 1):
                if np.sum(has_multiple_peaks[start:start + w_size]) >= w_hits:
                    detection_mask[start:start + w_size] = True

    return chunk_peaks, detection_mask
