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
// (C) COPYRIGHT International Business Machines Corp., 2003,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.war;

import com.ibm.websphere.samples.pbw.jpa.BackOrder;
import com.ibm.websphere.samples.pbw.jpa.Inventory;
import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * A class to hold a back order item's data.
 */
public class BackOrderItem implements java.io.Serializable {
	/**
	 * 
	 */
	private static final long serialVersionUID = 1L;
	private String name;
	private int inventoryQuantity;
	private String backOrderID; // from BackOrder
	private int quantity; // from BackOrder
	private String status; // from BackOrder
	private long lowDate; // from BackOrder
	private long orderDate; // from BackOrder
	private String supplierOrderID; // from BackOrder
	private Inventory inventory; // from BackOrder

	/**
	 * @see java.lang.Object#Object()
	 */
	/** Default constructor. */
	public BackOrderItem() {
	}

	/**
	 * Method BackOrderItem.
	 * 
	 * @param backOrderID
	 * @param inventoryID
	 * @param name
	 * @param quantity
	 * @param status
	 */
	public BackOrderItem(String backOrderID, Inventory inventoryID, String name, int quantity, String status) {
		this.backOrderID = backOrderID;
		this.inventory = inventoryID;
		this.name = name;
		this.quantity = quantity;
		this.status = status;
	}

	/**
	 * Method BackOrderItem.
	 * 
	 * @param backOrder
	 */
	public BackOrderItem(BackOrder backOrder) {
		try {
			this.backOrderID = backOrder.getBackOrderID();
			this.inventory = backOrder.getInventory();
			this.quantity = backOrder.getQuantity();
			this.status = backOrder.getStatus();
			this.lowDate = backOrder.getLowDate();
			this.orderDate = backOrder.getOrderDate();
			this.supplierOrderID = backOrder.getSupplierOrderID();
		} catch (Exception e) {
			Util.debug("BackOrderItem - Exception: " + e);
		}
	}

	/**
	 * Method getBackOrderID.
	 * 
	 * @return String
	 */
	public String getBackOrderID() {
		return backOrderID;
	}

	/**
	 * Method setBackOrderID.
	 * 
	 * @param backOrderID
	 */
	public void setBackOrderID(String backOrderID) {
		this.backOrderID = backOrderID;
	}

	/**
	 * Method getSupplierOrderID.
	 * 
	 * @return String
	 */
	public String getSupplierOrderID() {
		return supplierOrderID;
	}

	/**
	 * Method setSupplierOrderID.
	 * 
	 * @param supplierOrderID
	 */
	public void setSupplierOrderID(String supplierOrderID) {
		this.supplierOrderID = supplierOrderID;
	}

	/**
	 * Method setQuantity.
	 * 
	 * @param quantity
	 */
	public void setQuantity(int quantity) {
		this.quantity = quantity;
	}

	/**
	 * Method getInventoryID.
	 * 
	 * @return String
	 */
	public Inventory getInventory() {
		return inventory;
	}

	/**
	 * Method getName.
	 * 
	 * @return String
	 */
	public String getName() {
		return name;
	}

	/**
	 * Method setName.
	 * 
	 * @param name
	 */
	public void setName(String name) {
		this.name = name;
	}

	/**
	 * Method getQuantity.
	 * 
	 * @return int
	 */
	public int getQuantity() {
		return quantity;
	}

	/**
	 * Method getInventoryQuantity.
	 * 
	 * @return int
	 */
	public int getInventoryQuantity() {
		return inventoryQuantity;
	}

	/**
	 * Method setInventoryQuantity.
	 * 
	 * @param quantity
	 */
	public void setInventoryQuantity(int quantity) {
		this.inventoryQuantity = quantity;
	}

	/**
	 * Method getStatus.
	 * 
	 * @return String
	 */
	public String getStatus() {
		return status;
	}

	/**
	 * Method getLowDate.
	 * 
	 * @return long
	 */
	public long getLowDate() {
		return lowDate;
	}

	/**
	 * Method getOrderDate.
	 * 
	 * @return long
	 */
	public long getOrderDate() {
		return orderDate;
	}
}
