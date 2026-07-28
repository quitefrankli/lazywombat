(() => {
  const canvas = document.getElementById('gol-canvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  const wrap = canvas.parentElement;
  const playBtn = document.getElementById('gol-play');
  const playIcon = document.getElementById('gol-play-icon');
  const playLabel = document.getElementById('gol-play-label');
  const stepBtn = document.getElementById('gol-step');
  const resetBtn = document.getElementById('gol-reset');
  const randomBtn = document.getElementById('gol-random');
  const clearBtn = document.getElementById('gol-clear');
  const speedSlider = document.getElementById('gol-speed');
  const speedValue = document.getElementById('gol-speed-value');
  const dimsEl = document.getElementById('gol-dims');
  const aliveEl = document.getElementById('gol-alive');
  const genEl = document.getElementById('gol-gen');

  const CELL_PX = 14;
  const MIN_CELL_PX = 8;

  let cols = 0;
  let rows = 0;
  let grid = null;
  let next = null;
  let generation = 0;
  let running = false;
  let gensPerSec = Number.parseInt(speedSlider.value, 10);
  let lastStep = 0;

  function allocate(width, height) {
    const dpr = window.devicePixelRatio || 1;
    const cellPx = width < 480 ? MIN_CELL_PX : CELL_PX;
    const newCols = Math.max(8, Math.floor(width / cellPx));
    const newRows = Math.max(8, Math.floor(height / cellPx));

    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const oldGrid = grid;
    const oldCols = cols;
    const oldRows = rows;
    const newGrid = new Uint8Array(newCols * newRows);

    if (oldGrid) {
      const copyCols = Math.min(oldCols, newCols);
      const copyRows = Math.min(oldRows, newRows);
      for (let y = 0; y < copyRows; y += 1) {
        for (let x = 0; x < copyCols; x += 1) {
          newGrid[y * newCols + x] = oldGrid[y * oldCols + x];
        }
      }
    }

    cols = newCols;
    rows = newRows;
    grid = newGrid;
    next = new Uint8Array(cols * rows);
    dimsEl.textContent = `${cols} × ${rows}`;
  }

  function seedGlider() {
    grid.fill(0);
    const cx = Math.floor(cols / 4);
    const cy = Math.floor(rows / 4);
    const pattern = [[1, 0], [2, 1], [0, 2], [1, 2], [2, 2]];

    for (const [dx, dy] of pattern) {
      const x = (cx + dx) % cols;
      const y = (cy + dy) % rows;
      grid[y * cols + x] = 1;
    }
    generation = 0;
  }

  function randomize() {
    for (let i = 0; i < grid.length; i += 1) {
      grid[i] = Math.random() < 0.25 ? 1 : 0;
    }
    generation = 0;
  }

  function clearGrid() {
    grid.fill(0);
    generation = 0;
  }

  function step() {
    let alive = 0;
    for (let y = 0; y < rows; y += 1) {
      const yUp = (y - 1 + rows) % rows;
      const yDown = (y + 1) % rows;
      for (let x = 0; x < cols; x += 1) {
        const xLeft = (x - 1 + cols) % cols;
        const xRight = (x + 1) % cols;
        const neighbours =
          grid[yUp * cols + xLeft] + grid[yUp * cols + x] + grid[yUp * cols + xRight] +
          grid[y * cols + xLeft] + grid[y * cols + xRight] +
          grid[yDown * cols + xLeft] + grid[yDown * cols + x] + grid[yDown * cols + xRight];
        const current = grid[y * cols + x];
        const live = (current && (neighbours === 2 || neighbours === 3)) ||
          (!current && neighbours === 3) ? 1 : 0;

        next[y * cols + x] = live;
        alive += live;
      }
    }

    [grid, next] = [next, grid];
    generation += 1;
    aliveEl.textContent = alive;
    genEl.textContent = generation;
  }

  function countAlive() {
    let alive = 0;
    for (let i = 0; i < grid.length; i += 1) alive += grid[i];
    return alive;
  }

  function updateStats() {
    aliveEl.textContent = countAlive();
    genEl.textContent = generation;
  }

  function draw() {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const cellWidth = width / cols;
    const cellHeight = height / rows;

    ctx.fillStyle = '#F0FFF0';
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = '#2D4A3E';

    for (let y = 0; y < rows; y += 1) {
      for (let x = 0; x < cols; x += 1) {
        if (grid[y * cols + x]) {
          ctx.fillRect(x * cellWidth, y * cellHeight, cellWidth - 0.5, cellHeight - 0.5);
        }
      }
    }

    if (cellWidth >= 12) {
      ctx.strokeStyle = 'rgba(135, 168, 120, 0.12)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = 0; x <= cols; x += 1) {
        ctx.moveTo(x * cellWidth, 0);
        ctx.lineTo(x * cellWidth, height);
      }
      for (let y = 0; y <= rows; y += 1) {
        ctx.moveTo(0, y * cellHeight);
        ctx.lineTo(width, y * cellHeight);
      }
      ctx.stroke();
    }
  }

  function tick(timestamp) {
    requestAnimationFrame(tick);
    if (!running) return;

    const interval = 1000 / gensPerSec;
    if (timestamp - lastStep >= interval) {
      lastStep = timestamp;
      step();
      draw();
    }
  }

  function setRunning(value) {
    running = value;
    playIcon.className = value ? 'bi bi-pause-fill' : 'bi bi-play-fill';
    playLabel.textContent = value ? 'Pause' : 'Play';
  }

  function pointerCellFromEvent(event) {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const cellX = Math.floor(x / (rect.width / cols));
    const cellY = Math.floor(y / (rect.height / rows));
    if (cellX < 0 || cellX >= cols || cellY < 0 || cellY >= rows) return null;
    return [cellX, cellY];
  }

  let painting = false;
  let paintValue = 1;

  canvas.addEventListener('pointerdown', (event) => {
    const cell = pointerCellFromEvent(event);
    if (!cell) return;

    canvas.setPointerCapture(event.pointerId);
    painting = true;
    const [cellX, cellY] = cell;
    paintValue = grid[cellY * cols + cellX] ? 0 : 1;
    grid[cellY * cols + cellX] = paintValue;
    updateStats();
    draw();
    event.preventDefault();
  });

  canvas.addEventListener('pointermove', (event) => {
    if (!painting) return;
    const cell = pointerCellFromEvent(event);
    if (!cell) return;

    const [cellX, cellY] = cell;
    if (grid[cellY * cols + cellX] !== paintValue) {
      grid[cellY * cols + cellX] = paintValue;
      updateStats();
      draw();
    }
  });

  function endPaint(event) {
    painting = false;
    if (canvas.hasPointerCapture(event.pointerId)) {
      canvas.releasePointerCapture(event.pointerId);
    }
  }

  canvas.addEventListener('pointerup', endPaint);
  canvas.addEventListener('pointercancel', endPaint);
  playBtn.addEventListener('click', () => setRunning(!running));
  stepBtn.addEventListener('click', () => { step(); draw(); });
  resetBtn.addEventListener('click', () => { seedGlider(); updateStats(); draw(); });
  randomBtn.addEventListener('click', () => { randomize(); updateStats(); draw(); });
  clearBtn.addEventListener('click', () => { clearGrid(); updateStats(); draw(); });
  speedSlider.addEventListener('input', (event) => {
    gensPerSec = Number.parseInt(event.target.value, 10);
    speedValue.textContent = gensPerSec;
  });

  const resizeObserver = new ResizeObserver(() => {
    const width = wrap.clientWidth;
    const height = wrap.clientHeight;
    if (width <= 0 || height <= 0) return;

    allocate(width, height);
    updateStats();
    draw();
  });
  resizeObserver.observe(wrap);

  requestAnimationFrame(() => {
    allocate(wrap.clientWidth, wrap.clientHeight);
    seedGlider();
    updateStats();
    draw();
    requestAnimationFrame(tick);
  });
})();
