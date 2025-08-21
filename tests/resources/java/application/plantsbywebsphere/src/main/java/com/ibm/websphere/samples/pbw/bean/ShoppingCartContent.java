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
package com.ibm.websphere.samples.pbw.bean;

import java.util.Enumeration;
import java.util.Hashtable;

import com.ibm.websphere.samples.pbw.jpa.Inventory;

/**
 * A class to hold a shopping cart's contents.
 */
public class ShoppingCartContent implements java.io.Serializable {
	/**
	 * 
	 */
	private static final long serialVersionUID = 1L;
	private Hashtable<String, Integer> table = null;

	public ShoppingCartContent() {
		table = new Hashtable<String, Integer>();
	}

	/** Add the item to the shopping cart. */
	public void addItem(Inventory si) {
		table.put(si.getID(), new Integer(si.getQuantity()));
	}

	/** Update the item in the shopping cart. */
	public void updateItem(Inventory si) {
		table.put(si.getID(), new Integer(si.getQuantity()));
	}

	/** Remove the item from the shopping cart. */
	public void removeItem(Inventory si) {
		table.remove(si.getID());
	}

	/**
	 * Return the number of items in the cart.
	 *
	 * @return The number of items in the cart.
	 */
	public int size() {
		return table.size();
	}

	/**
	 * Return the inventory ID at the index given. The first element is at index 0, the second at
	 * index 1, and so on.
	 *
	 * @return The inventory ID at the index, or NULL if not present.
	 */
	public String getInventoryID(int index) {
		String retval = null;
		String inventoryID;
		int cnt = 0;
		for (Enumeration<String> myEnum = table.keys(); myEnum.hasMoreElements(); cnt++) {
			inventoryID = (String) myEnum.nextElement();
			if (index == cnt) {
				retval = inventoryID;
				break;
			}
		}
		return retval;
	}

	/**
	 * Return the quantity for the inventory ID given.
	 *
	 * @return The quantity for the inventory ID given..
	 *
	 */
	public int getQuantity(String inventoryID) {
		Integer quantity = (Integer) table.get(inventoryID);

		if (quantity == null)
			return 0;
		else
			return quantity.intValue();
	}

}
