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
package com.ibm.websphere.samples.pbw.jpa;

import javax.persistence.Entity;
import javax.persistence.GeneratedValue;
import javax.persistence.GenerationType;
import javax.persistence.Id;
import javax.persistence.JoinColumn;
import javax.persistence.NamedQueries;
import javax.persistence.NamedQuery;
import javax.persistence.OneToOne;
import javax.persistence.Table;
import javax.persistence.TableGenerator;

import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * Bean mapping for BACKORDER table.
 */
@Entity(name = "BackOrder")
@Table(name = "BACKORDER", schema = "APP")
@NamedQueries({ @NamedQuery(name = "findAllBackOrders", query = "select b from BackOrder b"),
		@NamedQuery(name = "findByInventoryID", query = "select b from BackOrder b where ((b.inventory.inventoryId = :id) and (b.status = 'Order Stock'))"),
		@NamedQuery(name = "removeAllBackOrder", query = "delete from BackOrder") })
public class BackOrder {
	@Id
	@GeneratedValue(strategy = GenerationType.TABLE, generator = "BackOrderSeq")
	@TableGenerator(name = "BackOrderSeq", table = "IDGENERATOR", pkColumnName = "IDNAME", pkColumnValue = "BACKORDER", valueColumnName = "IDVALUE")
	private String backOrderID;
	private int quantity;
	private String status;
	private long lowDate;
	private long orderDate;
	private String supplierOrderID; // missing table

	// relationships
	@OneToOne
	@JoinColumn(name = "INVENTORYID")
	private Inventory inventory;

	public BackOrder() {
	}

	public BackOrder(String backOrderID) {
		setBackOrderID(backOrderID);
	}

	public BackOrder(Inventory inventory, int quantity) {
			this.setInventory(inventory);
			this.setQuantity(quantity);
			this.setStatus(Util.STATUS_ORDERSTOCK);
			this.setLowDate(System.currentTimeMillis());
	}

	public String getBackOrderID() {
		return backOrderID;
	}

	public void setBackOrderID(String backOrderID) {
		this.backOrderID = backOrderID;
	}

	public long getLowDate() {
		return lowDate;
	}

	public void setLowDate(long lowDate) {
		this.lowDate = lowDate;
	}

	public long getOrderDate() {
		return orderDate;
	}

	public void setOrderDate(long orderDate) {
		this.orderDate = orderDate;
	}

	public int getQuantity() {
		return quantity;
	}

	public void setQuantity(int quantity) {
		this.quantity = quantity;
	}

	public void increateQuantity(int delta) {
		if (!(status.equals(Util.STATUS_ORDERSTOCK))) {
			Util.debug("BackOrderMgr.createBackOrder() - Backorders found but have already been ordered from the supplier");
			throw new RuntimeException("cannot increase order size for orders already in progress");
		}
		// Increase the BackOrder quantity for an existing Back Order.
		quantity = quantity + delta;
	}

	public String getStatus() {
		return status;
	}

	public void setStatus(String status) {
		this.status = status;
	}

	public String getSupplierOrderID() {
		return supplierOrderID;
	}

	public void setSupplierOrderID(String supplierOrderID) {
		this.supplierOrderID = supplierOrderID;
	}

	public Inventory getInventory() {
		return inventory;
	}

	public void setInventory(Inventory inventory) {
		this.inventory = inventory;
	}

}
