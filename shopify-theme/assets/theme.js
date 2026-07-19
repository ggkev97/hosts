(function () {
  'use strict';

  /* ----------------------------------------------------------------
     Mobile menu
  ---------------------------------------------------------------- */
  var menuToggle = document.querySelector('[data-menu-toggle]');
  var menuDrawer = document.querySelector('[data-menu-drawer]');

  if (menuToggle && menuDrawer) {
    menuToggle.addEventListener('click', function () {
      var open = menuDrawer.classList.toggle('is-open');
      menuToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  /* ----------------------------------------------------------------
     Cart drawer
  ---------------------------------------------------------------- */
  var drawer = document.getElementById('CartDrawer');

  function openDrawer() {
    if (!drawer) return;
    drawer.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  }

  function closeDrawer() {
    if (!drawer) return;
    drawer.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-cart-drawer-close]')) closeDrawer();

    var toggle = event.target.closest('[data-cart-toggle]');
    if (toggle && drawer) {
      event.preventDefault();
      openDrawer();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeDrawer();
  });

  function updateCartCount(count) {
    document.querySelectorAll('[data-cart-count]').forEach(function (el) {
      el.textContent = count;
      el.classList.toggle('hidden', count === 0);
    });
  }

  // The drawer lives in the layout (not a section), so we rebuild its
  // contents client-side from /cart.js data after every cart mutation.
  function renderDrawerFromCart(cart) {
    var container = drawer && drawer.querySelector('[data-cart-drawer-items]');
    if (!container) return;

    if (cart.item_count === 0) {
      container.innerHTML =
        '<p class="cart-drawer__empty">' + window.themeStrings.cartEmpty + '</p>' +
        '<a href="' + window.themeRoutes.allProducts + '" class="button button--secondary">' +
        window.themeStrings.continueShopping + '</a>';
      return;
    }

    var itemsHtml = cart.items
      .map(function (item, index) {
        var line = index + 1;
        var image = item.image
          ? '<img src="' + item.image.replace(/(\.[a-z]+)(\?|$)/, '_160x$1$2') + '" alt="" width="80" loading="lazy">'
          : '';
        var variantTitle =
          item.product_has_only_default_variant || item.variant_title === null
            ? ''
            : '<p class="cart-item__variant">' + item.variant_title + '</p>';
        return (
          '<li class="cart-item">' +
          '<a href="' + item.url + '" class="cart-item__media">' + image + '</a>' +
          '<div class="cart-item__details">' +
          '<a href="' + item.url + '" class="cart-item__title">' + item.product_title + '</a>' +
          variantTitle +
          '<div class="cart-item__row">' +
          '<div class="quantity">' +
          '<button type="button" class="quantity__button" data-cart-qty-change="-1" data-line="' + line + '" aria-label="&minus;">&minus;</button>' +
          '<input class="quantity__input" type="number" inputmode="numeric" value="' + item.quantity + '" min="0" data-cart-qty-input data-line="' + line + '">' +
          '<button type="button" class="quantity__button" data-cart-qty-change="1" data-line="' + line + '" aria-label="+">+</button>' +
          '</div>' +
          '<span class="cart-item__price">' + formatMoney(item.final_line_price) + '</span>' +
          '</div>' +
          '<button type="button" class="cart-item__remove" data-cart-remove data-line="' + line + '">' +
          window.themeStrings.remove +
          '</button>' +
          '</div>' +
          '</li>'
        );
      })
      .join('');

    container.innerHTML =
      '<ul class="cart-drawer__items" role="list">' + itemsHtml + '</ul>' +
      '<div class="cart-drawer__footer">' +
      '<div class="cart-drawer__subtotal"><span>' + window.themeStrings.subtotal + '</span><span>' +
      formatMoney(cart.total_price) + '</span></div>' +
      '<p class="cart-drawer__note">' + window.themeStrings.taxesNote + '</p>' +
      '<a href="' + window.themeRoutes.cart + '" class="button button--secondary">' + window.themeStrings.cartTitle + '</a>' +
      '<button type="button" class="button" onclick="window.location.href=\'' + window.themeRoutes.cart + '/checkout\'">' +
      window.themeStrings.checkout + '</button>' +
      '</div>';
  }

  function formatMoney(cents) {
    var format = window.themeMoneyFormat || '${{amount}}';
    var amount = (cents / 100).toFixed(2);
    return format.replace(/\{\{\s*amount[^}]*\}\}/, amount);
  }

  function fetchCart() {
    return fetch('/cart.js').then(function (res) {
      return res.json();
    });
  }

  function changeLine(line, quantity) {
    return fetch('/cart/change.js', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ line: line, quantity: quantity })
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (cart) {
        updateCartCount(cart.item_count);
        renderDrawerFromCart(cart);
        return cart;
      });
  }

  document.addEventListener('click', function (event) {
    var qtyButton = event.target.closest('[data-cart-qty-change]');
    if (qtyButton && qtyButton.closest('#CartDrawer')) {
      var line = parseInt(qtyButton.dataset.line, 10);
      var input = drawer.querySelector('[data-cart-qty-input][data-line="' + line + '"]');
      var current = input ? parseInt(input.value, 10) || 0 : 0;
      var next = Math.max(0, current + parseInt(qtyButton.dataset.cartQtyChange, 10));
      changeLine(line, next);
      return;
    }

    var removeButton = event.target.closest('[data-cart-remove]');
    if (removeButton && removeButton.closest('#CartDrawer')) {
      changeLine(parseInt(removeButton.dataset.line, 10), 0);
    }
  });

  document.addEventListener('change', function (event) {
    var input = event.target.closest('[data-cart-qty-input]');
    if (input && input.closest('#CartDrawer')) {
      changeLine(parseInt(input.dataset.line, 10), Math.max(0, parseInt(input.value, 10) || 0));
    }
  });

  /* ----------------------------------------------------------------
     Product form: variant selection + AJAX add to cart
  ---------------------------------------------------------------- */
  var productSection = document.querySelector('[data-product-section]');

  if (productSection) {
    var productJsonEl = productSection.querySelector('[data-product-json]');
    var productData = productJsonEl ? JSON.parse(productJsonEl.textContent) : null;
    var form = productSection.querySelector('[data-product-form]');
    var variantInput = productSection.querySelector('[data-variant-id]');
    var addButton = productSection.querySelector('[data-add-to-cart]');
    var addButtonText = productSection.querySelector('[data-add-to-cart-text]');
    var priceWrapper = productSection.querySelector('[data-price-wrapper]');

    function selectedOptions() {
      var options = [];
      productSection.querySelectorAll('.product__option').forEach(function (fieldset) {
        var checked = fieldset.querySelector('input[type="radio"]:checked');
        if (checked) options.push(checked.value);
      });
      return options;
    }

    function findVariant(options) {
      if (!productData) return null;
      return productData.variants.find(function (variant) {
        return variant.options.every(function (value, index) {
          return value === options[index];
        });
      });
    }

    function onOptionChange() {
      var variant = findVariant(selectedOptions());
      if (!variant) {
        addButton.disabled = true;
        addButtonText.textContent = window.themeStrings.unavailable;
        return;
      }
      variantInput.value = variant.id;
      addButton.disabled = !variant.available;
      addButtonText.textContent = variant.available
        ? window.themeStrings.addToCart
        : window.themeStrings.soldOut;
      if (priceWrapper) {
        var priceHtml = '<span class="price__regular">' + formatMoney(variant.price) + '</span>';
        if (variant.compare_at_price > variant.price) {
          priceHtml =
            '<span class="price__sale">' + formatMoney(variant.price) + '</span>' +
            '<s class="price__compare">' + formatMoney(variant.compare_at_price) + '</s>';
        }
        priceWrapper.innerHTML = '<div class="price">' + priceHtml + '</div>';
      }

      var url = new URL(window.location.href);
      url.searchParams.set('variant', variant.id);
      window.history.replaceState({}, '', url);
    }

    productSection.querySelectorAll('[data-option-input]').forEach(function (input) {
      input.addEventListener('change', onOptionChange);
    });

    productSection.querySelectorAll('[data-qty-change]').forEach(function (button) {
      button.addEventListener('click', function () {
        var input = productSection.querySelector('input[name="quantity"]');
        var next = Math.max(1, (parseInt(input.value, 10) || 1) + parseInt(button.dataset.qtyChange, 10));
        input.value = next;
      });
    });

    if (form) {
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        addButton.disabled = true;

        fetch('/cart/add.js', {
          method: 'POST',
          body: new FormData(form)
        })
          .then(function (res) {
            if (!res.ok) throw new Error('Add to cart failed');
            return fetchCart();
          })
          .then(function (cart) {
            updateCartCount(cart.item_count);
            renderDrawerFromCart(cart);
            addButton.disabled = false;
            if (window.themeSettings && window.themeSettings.cartDrawerEnabled) {
              openDrawer();
            }
          })
          .catch(function () {
            addButton.disabled = false;
            form.submit();
          });
      });
    }
  }

  /* ----------------------------------------------------------------
     Drop countdown
  ---------------------------------------------------------------- */
  document.querySelectorAll('[data-countdown]').forEach(function (el) {
    var target = new Date(el.dataset.countdown.replace(' ', 'T')).getTime();
    if (isNaN(target)) return;

    var days = el.querySelector('[data-cd-days]');
    var hours = el.querySelector('[data-cd-hours]');
    var mins = el.querySelector('[data-cd-mins]');
    var secs = el.querySelector('[data-cd-secs]');

    function pad(n) {
      return String(n).padStart(2, '0');
    }

    function tick() {
      var diff = target - Date.now();
      if (diff <= 0) {
        el.hidden = true;
        clearInterval(timer);
        return;
      }
      el.hidden = false;
      days.textContent = pad(Math.floor(diff / 86400000));
      hours.textContent = pad(Math.floor(diff / 3600000) % 24);
      mins.textContent = pad(Math.floor(diff / 60000) % 60);
      secs.textContent = pad(Math.floor(diff / 1000) % 60);
    }

    var timer = setInterval(tick, 1000);
    tick();
  });

  /* ----------------------------------------------------------------
     Collection sorting
  ---------------------------------------------------------------- */
  var sortSelect = document.querySelector('[data-sort-select]');
  if (sortSelect) {
    sortSelect.addEventListener('change', function () {
      var url = new URL(window.location.href);
      url.searchParams.set('sort_by', sortSelect.value);
      window.location.href = url.toString();
    });
  }
})();
