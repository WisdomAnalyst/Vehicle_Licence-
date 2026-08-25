// ============================================
// Vehicle Plate Detection - Frontend Script
// ============================================

const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');
const resultsArea = document.getElementById('resultsArea');
const resetBtn = document.getElementById('resetBtn');
const exportBtn = document.getElementById('exportBtn');
const previewImage = document.getElementById('previewImage');
const detectionCanvas = document.getElementById('detectionCanvas');
const loadingSpinner = document.getElementById('loadingSpinner');
const platesList = document.getElementById('platesList');
const plateCount = document.getElementById('plateCount');
const processingTime = document.getElementById('processingTime');
const avgConfidence = document.getElementById('avgConfidence');

let lastResult = null;

// Upload area click handler
uploadArea.addEventListener('click', () => fileInput.click());

// Drag and drop handlers
uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.classList.add('dragover');
});

uploadArea.addEventListener('dragleave', () => {
    uploadArea.classList.remove('dragover');
});

uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('dragover');

    const files = e.dataTransfer.files;
    if (files.length > 0) {
        handleFileUpload(files[0]);
    }
});

// File input change handler
fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFileUpload(e.target.files[0]);
    }
});

// Reset button handler
resetBtn.addEventListener('click', () => {
    resultsArea.style.display = 'none';
    uploadArea.style.display = 'flex';
    fileInput.value = '';
    platesList.innerHTML = '';
    lastResult = null;
});

// Export button handler — downloads detection details as a JSON file
exportBtn.addEventListener('click', () => {
    if (!lastResult) return;

    const exportData = {
        filename: lastResult.filename,
        processing_time_ms: Math.round(lastResult.processing_time * 1000),
        plate_count: (lastResult.detections || []).length,
        detections: (lastResult.detections || []).map(d => ({
            plate_number: d.plate_number || 'N/A',
            confidence: d.confidence,
            bbox: d.bbox
        }))
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const baseName = (lastResult.filename || 'detection').replace(/\.[^/.]+$/, '');
    a.download = `${baseName}-detections.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
});

// Handle file upload
async function handleFileUpload(file) {
    // Validate file type
    const allowedTypes = ['image/jpeg', 'image/png', 'image/bmp', 'image/webp'];
    if (!allowedTypes.includes(file.type)) {
        showError('Invalid file type. Please upload JPG, PNG, BMP, or WebP image.');
        return;
    }

    // Validate file size (10MB)
    const maxSize = 10 * 1024 * 1024;
    if (file.size > maxSize) {
        showError('File size exceeds 10MB limit.');
        return;
    }

    // Show loading state
    uploadArea.style.display = 'none';
    resultsArea.style.display = 'flex';
    loadingSpinner.style.display = 'flex';
    platesList.innerHTML = '';

    try {
        // Create FormData
        const formData = new FormData();
        formData.append('file', file);

        // Send to backend (API key is handled server-side)
        const response = await fetch('/api/detect', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Detection failed');
        }

        const result = await response.json();

        // Hide loading spinner
        loadingSpinner.style.display = 'none';

        // Display results
        displayResults(result);

    } catch (error) {
        loadingSpinner.style.display = 'none';
        showError(error.message || 'An error occurred during detection');
        resultsArea.style.display = 'none';
        uploadArea.style.display = 'flex';
    }
}

// Display detection results
function displayResults(result) {
    lastResult = result;

    const detections = result.detections || [];

    // Attach the onload handler BEFORE setting src, so we can never miss the
    // load event (a small base64 image can finish loading almost immediately,
    // which risks the handler being attached too late and the boxes either
    // never being drawn, or being drawn against a stale, no-longer-current image).
    previewImage.onload = () => drawDetectionBoxes(detections);

    // Clear any leftover boxes from a previous image immediately
    const ctx = detectionCanvas.getContext('2d');
    ctx.clearRect(0, 0, detectionCanvas.width, detectionCanvas.height);

    // Display image (this triggers onload above once it's actually loaded)
    previewImage.src = result.image;

    // Update statistics
    plateCount.textContent = detections.length;
    processingTime.textContent = `${(result.processing_time * 1000).toFixed(0)}ms`;

    // Calculate average confidence
    if (detections.length > 0) {
        const avgConf = (detections.reduce((sum, d) => sum + (d.confidence || 0), 0) / detections.length * 100).toFixed(1);
        avgConfidence.textContent = `${avgConf}%`;
    } else {
        avgConfidence.textContent = '0%';
    }

    // Display plates
    if (detections.length === 0) {
        platesList.innerHTML = '<div class="no-results">No license plates detected</div>';
        return;
    }

    platesList.innerHTML = detections.map((detection, index) => `
        <div class="plate-card">
            <div class="plate-number">
                <span class="plate-text">${detection.plate_number || 'N/A'}</span>
            </div>
            <div class="plate-info">
                <div class="info-row">
                    <span class="info-label">Confidence:</span>
                    <span class="info-value">${(detection.confidence * 100).toFixed(1)}%</span>
                </div>
                <div class="confidence-bar">
                    <div class="confidence-fill" style="width: ${detection.confidence * 100}%"></div>
                </div>
                <div class="info-row">
                    <span class="info-label">Position:</span>
                    <span class="info-value">${detection.bbox ? `[${detection.bbox.join(', ')}]` : 'N/A'}</span>
                </div>
            </div>
        </div>
    `).join('');
}

// Draw bounding boxes on the canvas overlay, scaled from the original
// image's pixel coordinates to however large the image is actually displayed
function drawDetectionBoxes(detections) {
    const displayWidth = previewImage.clientWidth;
    const displayHeight = previewImage.clientHeight;

    // .image-preview centers the image with flexbox, so when the image is
    // shorter/narrower than the container, there's blank space around it.
    // The canvas must align to the IMAGE's actual position, not the container's
    // top-left corner, or every box ends up offset by that centering gap.
    detectionCanvas.style.left = `${previewImage.offsetLeft}px`;
    detectionCanvas.style.top = `${previewImage.offsetTop}px`;

    detectionCanvas.width = displayWidth;
    detectionCanvas.height = displayHeight;
    detectionCanvas.style.width = `${displayWidth}px`;
    detectionCanvas.style.height = `${displayHeight}px`;

    const ctx = detectionCanvas.getContext('2d');
    ctx.clearRect(0, 0, displayWidth, displayHeight);

    if (!detections.length || !previewImage.naturalWidth) return;

    // Scale factor from original (natural) image pixels to displayed pixels
    const scaleX = displayWidth / previewImage.naturalWidth;
    const scaleY = displayHeight / previewImage.naturalHeight;

    detections.forEach((detection) => {
        if (!detection.bbox) return;
        const [x1, y1, x2, y2] = detection.bbox;

        const boxX = x1 * scaleX;
        const boxY = y1 * scaleY;
        const boxW = (x2 - x1) * scaleX;
        const boxH = (y2 - y1) * scaleY;

        // Box
        ctx.strokeStyle = '#00d4ff';
        ctx.lineWidth = 3;
        ctx.strokeRect(boxX, boxY, boxW, boxH);

        // Label background
        const label = `${detection.plate_number || 'plate'} ${((detection.confidence || 0) * 100).toFixed(0)}%`;
        ctx.font = '600 14px Inter, sans-serif';
        const textWidth = ctx.measureText(label).width;
        ctx.fillStyle = '#00d4ff';
        ctx.fillRect(boxX, Math.max(0, boxY - 22), textWidth + 12, 22);

        // Label text
        ctx.fillStyle = '#0f1419';
        ctx.fillText(label, boxX + 6, Math.max(14, boxY - 6));
    });
}

// Show error message
function showError(message) {
    const errorDiv = document.createElement('div');
    errorDiv.className = 'error-message';
    errorDiv.textContent = message;
    document.body.appendChild(errorDiv);

    setTimeout(() => {
        errorDiv.remove();
    }, 5000);
}

// Health check on page load
async function checkHealth() {
    try {
        const response = await fetch('/api/health');
        if (response.ok) {
            console.log('✓ API is healthy');
        }
    } catch (error) {
        console.error('Health check failed:', error);
    }
}

// Check health on page load
document.addEventListener('DOMContentLoaded', checkHealth);

// Redraw boxes if the window resizes while results are visible (image size changes)
window.addEventListener('resize', () => {
    if (resultsArea.style.display !== 'none' && lastResult && lastResult.detections && lastResult.detections.length) {
        drawDetectionBoxes(lastResult.detections);
    }
});