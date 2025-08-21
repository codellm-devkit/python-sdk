//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2001,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//

package com.ibm.websphere.samples.pbw.war;

import java.io.Serializable;
import java.text.NumberFormat;
import java.util.Locale;
import java.util.Objects;

import com.ibm.websphere.samples.pbw.jpa.Inventory;
import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * Provides backing bean support for the product web page. Accessed via the shopping bean.
 *
 */
public class ProductBean implements Serializable {
	private static final long serialVersionUID = 1L;
	private Inventory inventory;
	private int quantity;

	protected ProductBean(Inventory inventory) {
	    Objects.requireNonNull(inventory, "Inventory cannot be null");
		this.inventory = inventory;
		this.quantity = 1;
	}

	public String getCategoryName() {
		return Util.getCategoryString(this.inventory.getCategory());
	}

	public Inventory getInventory() {
		return this.inventory;
	}

	public String getMenuString() {
		String categoryString = getCategoryName();

		if (categoryString.equals("Flowers")) {
			return "banner:menu1";
		}

		else if (categoryString.equals("Fruits & Vegetables")) {
			return "banner:menu2";
		}

		else if (categoryString.equals("Trees")) {
			return "banner:menu3";
		}

		else {
			return "banner:menu4";
		}
	}

	public String getPrice() {
		return NumberFormat.getCurrencyInstance(Locale.US).format(new Float(this.inventory.getPrice()));
	}

	public int getQuantity() {
		return this.quantity;
	}

	public void setQuantity(int quantity) {
		this.quantity = quantity;
	}
}
