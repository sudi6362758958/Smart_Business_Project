// static/products/price_calc.js
(function(){
  function getCookie(name) {
    const cookies = document.cookie ? document.cookie.split(';') : [];
    for (let i = 0; i < cookies.length; i++) {
      const c = cookies[i].trim();
      if (c.startsWith(name + '=')) {
        return decodeURIComponent(c.substring(name.length + 1));
      }
    }
    return null;
  }
  const csrftoken = getCookie('csrftoken');

  function debounce(fn, wait) {
    let t;
    return function(...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  async function callApi(productId, quantity, unit) {
    const params = new URLSearchParams();
    params.append('product_id', productId);
    params.append('quantity', quantity);
    params.append('unit', unit);

    const resp = await fetch(window.priceCalcApiUrl || '/products/api/price-calculator/', {
      method: 'POST',
      headers: {
        'X-CSRFToken': csrftoken,
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      body: params.toString()
    });

    const data = await resp.json();
    return { resp, data };
  }

  document.addEventListener('DOMContentLoaded', function () {
    const productIdEl = document.getElementById('pc-product-id');
    if (!productIdEl) return;  // not on a product page

    const productId = productIdEl.value;
    const qtyInput = document.getElementById('pc-quantity');
    const unitSelect = document.getElementById('pc-unit');
    const resultSpan = document.getElementById('pc-total');
    const errorDiv = document.getElementById('pc-error');
    const calcBtn = document.getElementById('pc-calc-btn');

    async function updatePrice() {
      errorDiv.style.display = 'none';
      resultSpan.textContent = 'Calculating...';
      try {
        const quantity = qtyInput.value;
        const unit = unitSelect.value;

        if (!quantity || parseFloat(quantity) <= 0) {
          resultSpan.textContent = 'Enter valid quantity';
          return;
        }

        const { resp, data } = await callApi(productId, quantity, unit);
        if (!resp.ok) {
          const firstErr = data && data.errors ? Object.values(data.errors)[0] : null;
          const msg = Array.isArray(firstErr) ? firstErr[0] : (firstErr || 'Error');
          errorDiv.style.display = 'block';
          errorDiv.textContent = msg;
          resultSpan.textContent = '—';
          return;
        }

        if (data.ok) {
          resultSpan.textContent = '₹' + data.total;
        } else {
          resultSpan.textContent = '—';
        }
      } catch (err) {
        console.error(err);
        errorDiv.style.display = 'block';
        errorDiv.textContent = 'Network error';
        resultSpan.textContent = '—';
      }
    }

    const debouncedUpdate = debounce(updatePrice, 300);
    qtyInput.addEventListener('input', debouncedUpdate);
    unitSelect.addEventListener('change', debouncedUpdate);
    calcBtn.addEventListener('click', updatePrice);
    debouncedUpdate();
  });
})();
